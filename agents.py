# FILE: agents.py
# AI Fabric substrate: the Agent Registry and Task Queue, and the agents
# registered against them (Engineering Auditor, Observation Engine).
#
# This is deliberately NOT the full AI Fabric design (no Model Router, no
# Event Bus, no Evaluation Engine, no Scheduler). It is the smallest real
# substrate that lets agents run, produce reviewable results, and be
# observed from Mission Control - built on the same durable-queue pattern
# already proven by the Brain Outbox (oc_memory_outbox).
#
# Agents propose findings/observations; they never modify Engineering
# Memory decisions directly. Every result is a draft record a human
# reviews, exactly like every other "AI proposes, never auto-promotes"
# boundary in this project.
#
# Observation Engine's actual scan logic lives in observation.py, not
# here - agents.py only imports its runner function and registers it, the
# same way it would for any future agent. This keeps agents.py as pure
# registry/queue infrastructure rather than growing per-agent logic
# inline.

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from admin import require_admin_token
from observation import run_observation_engine

router = APIRouter(
    prefix="/api/v1/agents",
    tags=["AI Agents"],
    dependencies=[Depends(require_admin_token)],
)

DATABASE_URL = os.getenv("DATABASE_URL")

TASK_STATUSES = {"pending", "running", "done", "failed"}
FINDING_STATUSES = {"open", "acknowledged", "resolved"}


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def _table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            ) AS exists
            """,
            (table_name,),
        )
        return bool(cur.fetchone()["exists"])


_TABLES_READY = False


def ensure_agent_tables(conn) -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_agent_registry (
                agent_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                purpose TEXT,
                lifecycle_state TEXT NOT NULL DEFAULT 'active',
                enabled BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_agent_tasks (
                task_id TEXT PRIMARY KEY,
                agent_key TEXT NOT NULL REFERENCES oc_agent_registry(agent_key),
                status TEXT NOT NULL DEFAULT 'pending',
                triggered_by TEXT,
                result_summary TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_agent_task_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES oc_agent_tasks(task_id),
                event_type TEXT NOT NULL,
                message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_agent_findings (
                finding_id TEXT PRIMARY KEY,
                agent_key TEXT NOT NULL REFERENCES oc_agent_registry(agent_key),
                task_id TEXT REFERENCES oc_agent_tasks(task_id),
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                finding_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'warning',
                summary TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            INSERT INTO oc_agent_registry (agent_key, name, purpose, lifecycle_state, enabled)
            VALUES (
                'engineering_auditor',
                'Engineering Auditor',
                'Flags implemented Engineering Memory decisions that have no linked commit, PR, release, document, or task.',
                'active',
                true
            )
            ON CONFLICT (agent_key) DO NOTHING
            """
        )
        cur.execute(
            """
            INSERT INTO oc_agent_registry (agent_key, name, purpose, lifecycle_state, enabled)
            VALUES (
                'observation_engine',
                'Observation Engine',
                'Scans Engineering Memory, the Agent Registry, the Task Queue, Findings, Evaluation Engine, and Mission Brief state to record immutable observations as evidence.',
                'active',
                true
            )
            ON CONFLICT (agent_key) DO NOTHING
            """
        )
    _TABLES_READY = True


def new_id() -> str:
    return str(uuid.uuid4())


def log_task_event(conn, task_id: str, event_type: str, message: str = "") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO oc_agent_task_events (event_id, task_id, event_type, message)
            VALUES (%s, %s, %s, %s)
            """,
            (new_id(), task_id, event_type, message),
        )


# ---------- run logic: Engineering Auditor ----------

def run_engineering_auditor(conn, task_id: str) -> dict[str, Any]:
    """Flags implemented decisions with zero rows in oc_memory_decision_links.

    Reconciles on every run: decisions no longer missing links have their
    prior open finding auto-resolved, mirroring the reconciliation pattern
    already used for Engineering Memory's own findings-style data.

    Defensive by design: Engineering Auditor does not own
    oc_memory_decisions/oc_memory_decision_links (Engineering Memory does),
    so on a fresh database where no decision has ever been recorded, those
    tables may not exist yet. That is treated as "nothing to audit," not
    an error - the same defensive pattern used throughout this codebase
    (table_exists checks in app.py, _table_exists in calyx.py).
    """
    if not (_table_exists(conn, "oc_memory_decisions") and _table_exists(conn, "oc_memory_decision_links")):
        return {
            "implemented_decisions_checked": 0,
            "unlinked_found": 0,
            "new_findings": 0,
            "auto_resolved_findings": 0,
            "note": "Engineering Memory tables not present yet - nothing to audit.",
        }

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM oc_memory_decisions WHERE status = 'implemented'")
        implemented_count = cur.fetchone()["n"]

        cur.execute(
            """
            SELECT d.decision_id, d.title
            FROM oc_memory_decisions d
            LEFT JOIN oc_memory_decision_links l ON l.decision_id = d.decision_id
            WHERE d.status = 'implemented'
            GROUP BY d.decision_id, d.title
            HAVING COUNT(l.link_id) = 0
            """
        )
        unlinked = cur.fetchall()

    flagged_ids = {row["decision_id"] for row in unlinked}
    new_findings = 0

    for row in unlinked:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT finding_id FROM oc_agent_findings
                WHERE agent_key = 'engineering_auditor'
                  AND subject_type = 'decision'
                  AND subject_id = %s
                  AND finding_type = 'missing_implementation_link'
                  AND status = 'open'
                """,
                (row["decision_id"],),
            )
            already_open = cur.fetchone()
        if already_open:
            continue

        finding_id = new_id()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oc_agent_findings
                    (finding_id, agent_key, task_id, subject_type, subject_id,
                     finding_type, severity, summary, status)
                VALUES (%s, 'engineering_auditor', %s, 'decision', %s,
                        'missing_implementation_link', 'warning', %s, 'open')
                """,
                (
                    finding_id, task_id, row["decision_id"],
                    f"Decision '{row['title']}' is marked implemented but has no linked "
                    f"commit, PR, release, document, or task.",
                ),
            )
        new_findings += 1

    resolved_count = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT finding_id, subject_id FROM oc_agent_findings
            WHERE agent_key = 'engineering_auditor'
              AND finding_type = 'missing_implementation_link'
              AND status = 'open'
            """
        )
        currently_open = cur.fetchall()

    for row in currently_open:
        if row["subject_id"] not in flagged_ids:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE oc_agent_findings
                    SET status = 'resolved', updated_at = now()
                    WHERE finding_id = %s
                    """,
                    (row["finding_id"],),
                )
            resolved_count += 1

    return {
        "implemented_decisions_checked": implemented_count,
        "unlinked_found": len(unlinked),
        "new_findings": new_findings,
        "auto_resolved_findings": resolved_count,
    }


AGENT_RUNNERS = {
    "engineering_auditor": run_engineering_auditor,
    "observation_engine": run_observation_engine,
}


# ---------- request/response models ----------

class AgentOut(BaseModel):
    agent_key: str
    name: str
    purpose: Optional[str] = None
    lifecycle_state: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    last_task: Optional[dict[str, Any]] = None


class TaskOut(BaseModel):
    task_id: str
    agent_key: str
    status: str
    triggered_by: Optional[str] = None
    result_summary: Optional[str] = None
    attempts: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class FindingOut(BaseModel):
    finding_id: str
    agent_key: str
    task_id: Optional[str] = None
    subject_type: str
    subject_id: str
    finding_type: str
    severity: str
    summary: str
    status: str
    created_at: datetime
    updated_at: datetime


class FindingStatusUpdate(BaseModel):
    status: str


# ---------- agent registry endpoints ----------

@router.get("", response_model=list[AgentOut])
def list_agents():
    try:
        with get_conn() as conn:
            ensure_agent_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM oc_agent_registry ORDER BY agent_key")
                agents = cur.fetchall()
                out = []
                for agent in agents:
                    cur.execute(
                        "SELECT * FROM oc_agent_tasks WHERE agent_key = %s ORDER BY created_at DESC LIMIT 1",
                        (agent["agent_key"],),
                    )
                    last_task = cur.fetchone()
                    row = dict(agent)
                    row["last_task"] = dict(last_task) if last_task else None
                    out.append(row)
        return out
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List agents failed: {exc}")


@router.get("/{agent_key}", response_model=AgentOut)
def get_agent(agent_key: str):
    try:
        with get_conn() as conn:
            ensure_agent_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM oc_agent_registry WHERE agent_key = %s", (agent_key,))
                agent = cur.fetchone()
                if not agent:
                    raise HTTPException(status_code=404, detail="Agent not found")
                cur.execute(
                    "SELECT * FROM oc_agent_tasks WHERE agent_key = %s ORDER BY created_at DESC LIMIT 1",
                    (agent_key,),
                )
                last_task = cur.fetchone()
                row = dict(agent)
                row["last_task"] = dict(last_task) if last_task else None
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Get agent failed: {exc}")


@router.post("/{agent_key}/run", response_model=TaskOut, status_code=201)
def run_agent(agent_key: str):
    if agent_key not in AGENT_RUNNERS:
        raise HTTPException(status_code=400, detail=f"No runner implemented for agent '{agent_key}'")
    try:
        with get_conn() as conn:
            ensure_agent_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT agent_key, enabled FROM oc_agent_registry WHERE agent_key = %s", (agent_key,))
                agent = cur.fetchone()
                if not agent:
                    raise HTTPException(status_code=404, detail="Agent not found")
                if not agent["enabled"]:
                    raise HTTPException(status_code=409, detail="Agent is disabled")

            task_id = new_id()
            now = datetime.now(timezone.utc)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO oc_agent_tasks
                        (task_id, agent_key, status, triggered_by, attempts, started_at)
                    VALUES (%s, %s, 'running', 'manual', 1, %s)
                    """,
                    (task_id, agent_key, now),
                )
            log_task_event(conn, task_id, "started", "Manual run triggered")

            runner = AGENT_RUNNERS[agent_key]
            try:
                result = runner(conn, task_id)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE oc_agent_tasks
                        SET status = 'done', result_summary = %s, finished_at = now(), updated_at = now()
                        WHERE task_id = %s
                        """,
                        (json.dumps(result), task_id),
                    )
                log_task_event(conn, task_id, "done", json.dumps(result))
            except Exception as run_exc:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE oc_agent_tasks
                        SET status = 'failed', last_error = %s, finished_at = now(), updated_at = now()
                        WHERE task_id = %s
                        """,
                        (str(run_exc), task_id),
                    )
                log_task_event(conn, task_id, "failed", str(run_exc))

            with conn.cursor() as cur:
                cur.execute("SELECT * FROM oc_agent_tasks WHERE task_id = %s", (task_id,))
                row = cur.fetchone()
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Run agent failed: {exc}")


@router.get("/{agent_key}/tasks", response_model=list[TaskOut])
def list_tasks(agent_key: str, limit: int = Query(default=20, ge=1, le=100)):
    try:
        with get_conn() as conn:
            ensure_agent_tables(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM oc_agent_tasks WHERE agent_key = %s ORDER BY created_at DESC LIMIT %s",
                    (agent_key, limit),
                )
                return cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List tasks failed: {exc}")


@router.get("/{agent_key}/findings", response_model=list[FindingOut])
def list_findings(agent_key: str, status: Optional[str] = Query(default=None), limit: int = Query(default=50, ge=1, le=200)):
    try:
        with get_conn() as conn:
            ensure_agent_tables(conn)
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        """
                        SELECT * FROM oc_agent_findings
                        WHERE agent_key = %s AND status = %s
                        ORDER BY created_at DESC LIMIT %s
                        """,
                        (agent_key, status, limit),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM oc_agent_findings WHERE agent_key = %s ORDER BY created_at DESC LIMIT %s",
                        (agent_key, limit),
                    )
                return cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List findings failed: {exc}")


@router.patch("/findings/{finding_id}", response_model=FindingOut)
def update_finding_status(finding_id: str, payload: FindingStatusUpdate):
    if payload.status not in FINDING_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(FINDING_STATUSES)}")
    try:
        with get_conn() as conn:
            ensure_agent_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT finding_id FROM oc_agent_findings WHERE finding_id = %s", (finding_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Finding not found")
                cur.execute(
                    """
                    UPDATE oc_agent_findings
                    SET status = %s, updated_at = now()
                    WHERE finding_id = %s
                    RETURNING *
                    """,
                    (payload.status, finding_id),
                )
                row = cur.fetchone()
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Update finding failed: {exc}")
