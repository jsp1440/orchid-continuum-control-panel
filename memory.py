# FILE: memory.py
# Engineering Memory + Brain Outbox.
#
# Control Panel is the local system of record for engineering decisions until
# a live Brain sync integration exists. Decisions are recorded here first;
# "queueing" a decision for Brain sync writes a durable outbox row instead of
# calling the Brain directly. If BRAIN_SYNC_ENDPOINT is not configured, the
# outbox entry simply stays "pending" - nothing is lost and nothing breaks.

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/memory", tags=["Engineering Memory"])

DATABASE_URL = os.getenv("DATABASE_URL")

DECISION_STATUSES = {
    "proposed", "under_review", "accepted", "implemented",
    "deprecated", "superseded", "rejected",
}
SYNC_STATUSES = {"pending", "sent", "confirmed", "failed"}

# Valid forward transitions. Anything not listed here is rejected by the
# status-change endpoint - this is intentionally a small, explicit state
# machine rather than a free-form status field.
DECISION_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"under_review", "rejected"},
    "under_review": {"accepted", "rejected"},
    "accepted": {"implemented", "deprecated", "superseded"},
    "implemented": {"deprecated", "superseded"},
    "deprecated": {"superseded"},
    "superseded": set(),
    "rejected": set(),
}

RELATIONSHIP_TYPES = {"supersedes", "parent_of", "conflicts_with", "related_to"}
LINK_TYPES = {"task", "finding", "commit", "pull_request", "release", "document", "external_url"}


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


_TABLES_READY = False


def ensure_memory_tables(conn) -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_memory_decisions (
                decision_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                context TEXT,
                decision TEXT NOT NULL,
                rationale TEXT,
                alternatives_considered JSONB NOT NULL DEFAULT '[]'::jsonb,
                affected_systems JSONB NOT NULL DEFAULT '[]'::jsonb,
                status TEXT NOT NULL DEFAULT 'proposed',
                created_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            "ALTER TABLE oc_memory_decisions ADD COLUMN IF NOT EXISTS governance_refs JSONB NOT NULL DEFAULT '[]'::jsonb"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_memory_decision_relationships (
                relationship_id TEXT PRIMARY KEY,
                from_decision_id TEXT NOT NULL REFERENCES oc_memory_decisions(decision_id),
                relationship_type TEXT NOT NULL,
                to_decision_id TEXT NOT NULL REFERENCES oc_memory_decisions(decision_id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_by TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_memory_decision_links (
                link_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL REFERENCES oc_memory_decisions(decision_id),
                link_type TEXT NOT NULL,
                link_ref TEXT NOT NULL,
                label TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_memory_outbox (
                outbox_id TEXT PRIMARY KEY,
                payload_type TEXT NOT NULL DEFAULT 'engineering_decision',
                payload_json JSONB NOT NULL,
                destination TEXT NOT NULL DEFAULT 'orchid_continuum_brain',
                sync_status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TIMESTAMPTZ,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_memory_outbox_events (
                event_id TEXT PRIMARY KEY,
                outbox_id TEXT NOT NULL REFERENCES oc_memory_outbox(outbox_id),
                event_type TEXT NOT NULL,
                message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    _TABLES_READY = True


def new_id() -> str:
    return str(uuid.uuid4())


def log_outbox_event(conn, outbox_id: str, event_type: str, message: str = "") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO oc_memory_outbox_events (event_id, outbox_id, event_type, message)
            VALUES (%s, %s, %s, %s)
            """,
            (new_id(), outbox_id, event_type, message),
        )


# ---------- request/response models ----------

class DecisionCreate(BaseModel):
    title: str
    context: str = ""
    decision: str
    rationale: str = ""
    alternatives_considered: list[dict[str, Any]] = Field(default_factory=list)
    affected_systems: list[str] = Field(default_factory=list)
    governance_refs: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "proposed"
    created_by: str = "unknown"


class DecisionOut(BaseModel):
    decision_id: str
    title: str
    context: Optional[str] = None
    decision: str
    rationale: Optional[str] = None
    alternatives_considered: list[Any] = Field(default_factory=list)
    affected_systems: list[Any] = Field(default_factory=list)
    governance_refs: list[Any] = Field(default_factory=list)
    status: str
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class StatusUpdate(BaseModel):
    status: str
    changed_by: str = "unknown"


class RelationshipCreate(BaseModel):
    relationship_type: str
    to_decision_id: str
    created_by: str = "unknown"


class RelationshipOut(BaseModel):
    relationship_id: str
    from_decision_id: str
    relationship_type: str
    to_decision_id: str
    created_at: datetime
    created_by: Optional[str] = None


class LinkCreate(BaseModel):
    link_type: str
    link_ref: str
    label: str = ""


class LinkOut(BaseModel):
    link_id: str
    decision_id: str
    link_type: str
    link_ref: str
    label: Optional[str] = None
    created_at: datetime


class OutboxOut(BaseModel):
    outbox_id: str
    payload_type: str
    payload_json: dict[str, Any]
    destination: str
    sync_status: str
    attempts: int
    last_attempt_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MarkFailedRequest(BaseModel):
    error: str = "unspecified error"


# ---------- decision endpoints ----------

@router.post("/decisions", response_model=DecisionOut, status_code=201)
def create_decision(payload: DecisionCreate):
    if payload.status not in DECISION_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(DECISION_STATUSES)}")
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            decision_id = new_id()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO oc_memory_decisions
                        (decision_id, title, context, decision, rationale,
                         alternatives_considered, affected_systems, governance_refs, status, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)
                    RETURNING *
                    """,
                    (
                        decision_id, payload.title, payload.context, payload.decision,
                        payload.rationale, json.dumps(payload.alternatives_considered),
                        json.dumps(payload.affected_systems), json.dumps(payload.governance_refs),
                        payload.status, payload.created_by,
                    ),
                )
                row = cur.fetchone()
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Create decision failed: {exc}")


@router.get("/decisions", response_model=list[DecisionOut])
def list_decisions(status: Optional[str] = Query(default=None), limit: int = Query(default=50, ge=1, le=200)):
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        "SELECT * FROM oc_memory_decisions WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                        (status, limit),
                    )
                else:
                    cur.execute("SELECT * FROM oc_memory_decisions ORDER BY created_at DESC LIMIT %s", (limit,))
                return cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List decisions failed: {exc}")


@router.get("/decisions/{decision_id}", response_model=DecisionOut)
def get_decision(decision_id: str):
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM oc_memory_decisions WHERE decision_id = %s", (decision_id,))
                row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Decision not found")
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Get decision failed: {exc}")


# ---------- lifecycle status transitions ----------

@router.patch("/decisions/{decision_id}/status", response_model=DecisionOut)
def update_decision_status(decision_id: str, payload: StatusUpdate):
    if payload.status not in DECISION_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(DECISION_STATUSES)}")
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM oc_memory_decisions WHERE decision_id = %s", (decision_id,))
                existing = cur.fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Decision not found")

                current_status = existing["status"]
                allowed = DECISION_TRANSITIONS.get(current_status, set())
                if payload.status not in allowed:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Invalid transition from '{current_status}' to '{payload.status}'. "
                            f"Allowed next states: {sorted(allowed) or 'none (terminal state)'}"
                        ),
                    )

                cur.execute(
                    """
                    UPDATE oc_memory_decisions
                    SET status = %s, updated_at = now()
                    WHERE decision_id = %s
                    RETURNING *
                    """,
                    (payload.status, decision_id),
                )
                row = cur.fetchone()
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Update decision status failed: {exc}")


# ---------- decision relationships ----------

@router.post("/decisions/{decision_id}/relationships", response_model=RelationshipOut, status_code=201)
def create_relationship(decision_id: str, payload: RelationshipCreate):
    if payload.relationship_type not in RELATIONSHIP_TYPES:
        raise HTTPException(status_code=400, detail=f"relationship_type must be one of {sorted(RELATIONSHIP_TYPES)}")
    if payload.to_decision_id == decision_id:
        raise HTTPException(status_code=400, detail="A decision cannot have a relationship to itself")
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT decision_id FROM oc_memory_decisions WHERE decision_id = %s", (decision_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Decision not found")
                cur.execute("SELECT decision_id FROM oc_memory_decisions WHERE decision_id = %s", (payload.to_decision_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="to_decision_id not found")

                relationship_id = new_id()
                cur.execute(
                    """
                    INSERT INTO oc_memory_decision_relationships
                        (relationship_id, from_decision_id, relationship_type, to_decision_id, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (relationship_id, decision_id, payload.relationship_type, payload.to_decision_id, payload.created_by),
                )
                row = cur.fetchone()
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Create relationship failed: {exc}")


@router.get("/decisions/{decision_id}/relationships", response_model=list[RelationshipOut])
def list_relationships(decision_id: str):
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT decision_id FROM oc_memory_decisions WHERE decision_id = %s", (decision_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Decision not found")
                cur.execute(
                    """
                    SELECT * FROM oc_memory_decision_relationships
                    WHERE from_decision_id = %s OR to_decision_id = %s
                    ORDER BY created_at DESC
                    """,
                    (decision_id, decision_id),
                )
                return cur.fetchall()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List relationships failed: {exc}")


# ---------- decision links ----------

@router.post("/decisions/{decision_id}/links", response_model=LinkOut, status_code=201)
def create_link(decision_id: str, payload: LinkCreate):
    if payload.link_type not in LINK_TYPES:
        raise HTTPException(status_code=400, detail=f"link_type must be one of {sorted(LINK_TYPES)}")
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT decision_id FROM oc_memory_decisions WHERE decision_id = %s", (decision_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Decision not found")

                link_id = new_id()
                cur.execute(
                    """
                    INSERT INTO oc_memory_decision_links
                        (link_id, decision_id, link_type, link_ref, label)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (link_id, decision_id, payload.link_type, payload.link_ref, payload.label),
                )
                row = cur.fetchone()
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Create link failed: {exc}")


@router.get("/decisions/{decision_id}/links", response_model=list[LinkOut])
def list_links(decision_id: str):
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT decision_id FROM oc_memory_decisions WHERE decision_id = %s", (decision_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Decision not found")
                cur.execute(
                    "SELECT * FROM oc_memory_decision_links WHERE decision_id = %s ORDER BY created_at DESC",
                    (decision_id,),
                )
                return cur.fetchall()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List links failed: {exc}")


# ---------- best-effort Brain sync (never raises; missing endpoint = stay pending) ----------

def _attempt_brain_sync(conn, outbox_id: str, payload: dict[str, Any]) -> None:
    endpoint = (os.getenv("BRAIN_SYNC_ENDPOINT") or "").strip()
    if not endpoint:
        log_outbox_event(conn, outbox_id, "sync_skipped", "BRAIN_SYNC_ENDPOINT not configured; message left pending")
        return

    token = (os.getenv("BRAIN_SYNC_TOKEN") or "").strip()
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    now = datetime.now(timezone.utc)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if not (200 <= resp.status < 300):
                raise urllib.error.HTTPError(endpoint, resp.status, "non-2xx response from Brain sync endpoint", None, None)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE oc_memory_outbox
                    SET sync_status='sent', attempts=attempts+1, last_attempt_at=%s, last_error=NULL, updated_at=now()
                    WHERE outbox_id=%s
                    """,
                    (now, outbox_id),
                )
            log_outbox_event(conn, outbox_id, "sent", f"HTTP {resp.status}")
    except Exception as exc:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oc_memory_outbox
                SET sync_status='failed', attempts=attempts+1, last_attempt_at=%s, last_error=%s, updated_at=now()
                WHERE outbox_id=%s
                """,
                (now, str(exc), outbox_id),
            )
        log_outbox_event(conn, outbox_id, "failed", str(exc))


@router.post("/decisions/{decision_id}/queue-brain-sync", response_model=OutboxOut, status_code=201)
def queue_brain_sync(decision_id: str):
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM oc_memory_decisions WHERE decision_id = %s", (decision_id,))
                decision = cur.fetchone()
            if not decision:
                raise HTTPException(status_code=404, detail="Decision not found")

            payload = {
                "decision_id": decision["decision_id"],
                "title": decision["title"],
                "context": decision["context"],
                "decision": decision["decision"],
                "rationale": decision["rationale"],
                "alternatives_considered": decision["alternatives_considered"],
                "affected_systems": decision["affected_systems"],
                "status": decision["status"],
                "created_by": decision["created_by"],
                "created_at": decision["created_at"].isoformat() if decision["created_at"] else None,
            }

            outbox_id = new_id()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO oc_memory_outbox
                        (outbox_id, payload_type, payload_json, destination, sync_status)
                    VALUES (%s, 'engineering_decision', %s::jsonb, 'orchid_continuum_brain', 'pending')
                    """,
                    (outbox_id, json.dumps(payload)),
                )
            log_outbox_event(conn, outbox_id, "queued", "Queued for Brain sync")

            _attempt_brain_sync(conn, outbox_id, payload)

            with conn.cursor() as cur:
                cur.execute("SELECT * FROM oc_memory_outbox WHERE outbox_id = %s", (outbox_id,))
                row = cur.fetchone()
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Queue Brain sync failed: {exc}")


# ---------- outbox endpoints ----------

@router.get("/outbox", response_model=list[OutboxOut])
def list_outbox(sync_status: Optional[str] = Query(default=None), limit: int = Query(default=50, ge=1, le=200)):
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                if sync_status:
                    cur.execute(
                        "SELECT * FROM oc_memory_outbox WHERE sync_status = %s ORDER BY created_at DESC LIMIT %s",
                        (sync_status, limit),
                    )
                else:
                    cur.execute("SELECT * FROM oc_memory_outbox ORDER BY created_at DESC LIMIT %s", (limit,))
                return cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List outbox failed: {exc}")


def _update_outbox_status(outbox_id: str, sync_status: str, error: Optional[str] = None):
    try:
        with get_conn() as conn:
            ensure_memory_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT outbox_id FROM oc_memory_outbox WHERE outbox_id = %s", (outbox_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Outbox entry not found")
                cur.execute(
                    """
                    UPDATE oc_memory_outbox
                    SET sync_status=%s, last_error=%s, last_attempt_at=now(), updated_at=now(),
                        attempts = attempts + CASE WHEN %s IN ('sent', 'failed') THEN 1 ELSE 0 END
                    WHERE outbox_id=%s
                    RETURNING *
                    """,
                    (sync_status, error, sync_status, outbox_id),
                )
                row = cur.fetchone()
            log_outbox_event(conn, outbox_id, sync_status, error or "")
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Update outbox status failed: {exc}")


@router.post("/outbox/{outbox_id}/mark-sent", response_model=OutboxOut)
def mark_outbox_sent(outbox_id: str):
    return _update_outbox_status(outbox_id, "sent")


@router.post("/outbox/{outbox_id}/mark-confirmed", response_model=OutboxOut)
def mark_outbox_confirmed(outbox_id: str):
    return _update_outbox_status(outbox_id, "confirmed")


@router.post("/outbox/{outbox_id}/mark-failed", response_model=OutboxOut)
def mark_outbox_failed(outbox_id: str, payload: MarkFailedRequest):
    return _update_outbox_status(outbox_id, "failed", payload.error)
