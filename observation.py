# FILE: observation.py
# Observation Engine Phase 1 - the evidence acquisition layer for Calyx.
#
# Observations are immutable evidence: a record of a fact Calyx directly
# observed, at a point in time, citing exactly where it came from.
# Evaluation Engine (evaluation.py) interprets/scores; Engineering Memory
# (memory.py) stores decisions; this module only records what is real and
# currently true, and honestly says nothing about domains it cannot see
# (Scientific literature, Pollinator/Mycorrhizal networks, Collaboration -
# no data source for any of these exists anywhere in this repository yet).
#
# Reuses existing infrastructure rather than duplicating it:
#   - runs through the existing Agent Registry / Task Queue
#     (registered as agent_key "observation_engine" in agents.py,
#     executed via the same POST /api/v1/agents/{key}/run every other
#     agent uses - no new run/task/event machinery here)
#   - only adds genuinely new surface: querying observation RECORDS
#     themselves, a concept agents.py has no notion of.
#
# Reconciliation (dedup / reaffirm / supersede) mirrors the exact pattern
# already proven in agents.py's Engineering Auditor: an observation's
# evidence id encodes its identity, so a changed fact is naturally a new
# id (superseding the old one) rather than an in-place edit - observations
# are never rewritten, only superseded.

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from admin import require_admin_token

router = APIRouter(
    prefix="/api/v1/observations",
    tags=["Observation Engine"],
    dependencies=[Depends(require_admin_token)],
)

DATABASE_URL = os.getenv("DATABASE_URL")

OBSERVATION_STATUSES = {"active", "superseded", "archived"}
OBSERVATION_SEVERITIES = {"info", "warning", "critical"}
OBSERVATION_CONFIDENCES = {"high", "medium", "low"}

# Snapshot-type sources are intentionally exempt from dedup - see design
# note in the module docstring. Their evidence id already encodes the
# scan, so every scan naturally accumulates as permanent history.
SNAPSHOT_SOURCES = {"evaluation_engine", "mission_brief"}

# The observation registry: a fixed, code-reviewed catalog of what Phase 1
# can and cannot observe. Deliberately a constant, not a database table -
# see design note on why a dynamic registry would duplicate the Agent
# Registry's actual purpose without a demonstrated need.
OBSERVATION_SOURCES: list[dict[str, Any]] = [
    {"key": "engineering_memory", "domain": "engineering", "available": True,
     "description": "Engineering Memory decisions awaiting action (proposed/under_review)."},
    {"key": "agent_registry", "domain": "engineering", "available": True,
     "description": "Registered agents and their enabled state."},
    {"key": "task_queue", "domain": "engineering", "available": True,
     "description": "Failed agent task runs."},
    {"key": "agent_findings", "domain": "engineering", "available": True,
     "description": "Open findings from any registered agent."},
    {"key": "evaluation_engine", "domain": "mission_progress", "available": True,
     "description": "Point-in-time snapshot of each Evaluation Engine domain score."},
    {"key": "mission_brief", "domain": "mission_progress", "available": True,
     "description": "Point-in-time snapshot of top-level Mission Brief counts."},
    {"key": "repository_metadata", "domain": "engineering", "available": True,
     "description": "Deployed commit (RENDER_GIT_COMMIT), when running on Render."},
    {"key": "health_endpoint", "domain": "engineering", "available": True,
     "description": "Direct database connectivity check performed by Calyx itself."},
    {"key": "scientific_literature", "domain": "scientific", "available": False,
     "description": "No literature data source exists in this repository yet."},
    {"key": "pollinator_network", "domain": "scientific", "available": False,
     "description": "No pollinator relationship data source exists in this repository yet."},
    {"key": "mycorrhizal_network", "domain": "scientific", "available": False,
     "description": "No mycorrhizal relationship data source exists in this repository yet."},
    {"key": "collaboration", "domain": "collaboration", "available": False,
     "description": "No researcher/project/collaboration data source exists in this repository yet."},
]


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def new_id() -> str:
    return str(uuid.uuid4())


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


def ensure_observation_tables(conn) -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_observations (
                observation_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                domain TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                confidence TEXT NOT NULL DEFAULT 'high',
                status TEXT NOT NULL DEFAULT 'active',
                description TEXT NOT NULL,
                evidence JSONB NOT NULL,
                related_objects JSONB NOT NULL DEFAULT '[]'::jsonb,
                recommended_action TEXT,
                dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
                dedup_key TEXT NOT NULL,
                scan_task_id TEXT,
                first_observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oc_observation_events (
                event_id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL REFERENCES oc_observations(observation_id),
                event_type TEXT NOT NULL,
                message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    _TABLES_READY = True


def log_observation_event(conn, observation_id: str, event_type: str, message: str = "") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO oc_observation_events (event_id, observation_id, event_type, message)
            VALUES (%s, %s, %s, %s)
            """,
            (new_id(), observation_id, event_type, message),
        )


# ---------- pure fact detection (no DB access - unit-testable) ----------
#
# Each detector takes already-fetched rows and returns a list of "facts":
# plain dicts with description/domain/severity/confidence/evidence/
# related_objects/recommended_action/dependencies. Evidence ids for
# "state" facts encode current identity (so a change is a new fact,
# naturally superseding the old one); evidence ids for "snapshot" facts
# encode the scan itself (so every scan accumulates as permanent history).

def detect_pending_decisions(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for d in decisions:
        if d.get("status") in ("proposed", "under_review"):
            facts.append({
                "description": f"Decision '{d['title']}' is {d['status']} and awaiting action.",
                "domain": "engineering", "severity": "info", "confidence": "high",
                "evidence": {"type": "decision", "id": d["decision_id"]},
                "related_objects": [], "recommended_action": None, "dependencies": [],
            })
    return facts


def detect_registered_agents(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for a in agents:
        enabled = bool(a.get("enabled"))
        facts.append({
            "description": f"Agent '{a['name']}' is registered ({'enabled' if enabled else 'disabled'}).",
            "domain": "engineering", "severity": "info" if enabled else "warning", "confidence": "high",
            "evidence": {"type": "agent", "id": a["agent_key"]},
            "related_objects": [], "recommended_action": None, "dependencies": [],
        })
    return facts


def detect_failed_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for t in tasks:
        if t.get("status") == "failed":
            facts.append({
                "description": f"{t['agent_key']} run failed: {t.get('last_error') or 'unknown error'}",
                "domain": "engineering", "severity": "warning", "confidence": "high",
                "evidence": {"type": "task", "id": t["task_id"]},
                "related_objects": [{"type": "agent", "id": t["agent_key"]}],
                "recommended_action": None, "dependencies": [],
            })
    return facts


def detect_open_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for f in findings:
        if f.get("status") == "open":
            related = [{"type": "agent", "id": f["agent_key"]}] if f.get("agent_key") else []
            facts.append({
                "description": f["summary"],
                "domain": "engineering", "severity": f.get("severity", "warning"), "confidence": "high",
                "evidence": {"type": "finding", "id": f["finding_id"]},
                "related_objects": related, "recommended_action": None, "dependencies": [],
            })
    return facts


def detect_domain_score_snapshots(domain_scores: dict[str, Any], scan_task_id: str) -> list[dict[str, Any]]:
    facts = []
    for domain_key, d in domain_scores.items():
        score = d.get("score")
        facts.append({
            "description": f"{d['label']}: {score if score is not None else 'no data available'}",
            "domain": domain_key,
            "severity": "info",
            "confidence": "high" if score is not None else "low",
            "evidence": {"type": "domain_score_snapshot", "id": f"{domain_key}@{scan_task_id}"},
            "related_objects": [], "recommended_action": None, "dependencies": [],
        })
    return facts


def detect_mission_brief_snapshot(counts: dict[str, Any], scan_task_id: str) -> list[dict[str, Any]]:
    return [{
        "description": (
            f"Mission Brief snapshot: {counts['decisions_total']} decisions, "
            f"{counts['findings_open']} open findings, {counts['tasks_failed']} failed tasks."
        ),
        "domain": "mission_progress", "severity": "info", "confidence": "high",
        "evidence": {"type": "mission_brief_snapshot", "id": f"counts@{scan_task_id}"},
        "related_objects": [], "recommended_action": None, "dependencies": [],
    }]


def detect_repository_metadata() -> list[dict[str, Any]]:
    commit = (os.getenv("RENDER_GIT_COMMIT") or "").strip()
    if not commit:
        return [{
            "description": "Deployed commit is unknown - RENDER_GIT_COMMIT is not set (likely not running on Render).",
            "domain": "engineering", "severity": "info", "confidence": "low",
            "evidence": {"type": "repository_metadata", "id": "commit_unknown"},
            "related_objects": [], "recommended_action": None, "dependencies": [],
        }]
    return [{
        "description": f"Deployed commit is {commit}.",
        "domain": "engineering", "severity": "info", "confidence": "high",
        "evidence": {"type": "repository_metadata", "id": f"commit_{commit}"},
        "related_objects": [], "recommended_action": None, "dependencies": [],
    }]


def detect_health(db_reachable: bool) -> list[dict[str, Any]]:
    status = "reachable" if db_reachable else "unreachable"
    return [{
        "description": f"Database connectivity check: {status}.",
        "domain": "engineering", "severity": "info" if db_reachable else "critical",
        "confidence": "high",
        "evidence": {"type": "health_check", "id": f"database_{status}"},
        "related_objects": [], "recommended_action": None if db_reachable else "Investigate database connectivity immediately.",
        "dependencies": [],
    }]


# ---------- pure reconciliation (no DB access - unit-testable) ----------

def compute_dedup_key(source: str, evidence: dict[str, Any]) -> str:
    return f"{source}:{evidence['type']}:{evidence['id']}"


def reconcile(source: str, facts: list[dict[str, Any]], existing_active: list[dict[str, Any]]) -> dict[str, Any]:
    """Given newly detected facts and the currently-active observations for
    this source, decides what to insert, what to reaffirm (already active,
    unchanged identity), and what to supersede (was active, no longer
    detected). Snapshot sources skip supersede entirely - their facts are
    permanent history, not "current state" that can become false.
    """
    existing_by_key = {o["dedup_key"]: o for o in existing_active}
    detected_keys: set[str] = set()
    to_insert = []
    reaffirm_ids = []

    for fact in facts:
        key = compute_dedup_key(source, fact["evidence"])
        detected_keys.add(key)
        if key in existing_by_key:
            reaffirm_ids.append(existing_by_key[key]["observation_id"])
        else:
            to_insert.append({**fact, "dedup_key": key})

    if source in SNAPSHOT_SOURCES:
        supersede_ids: list[str] = []
    else:
        supersede_ids = [o["observation_id"] for o in existing_active if o["dedup_key"] not in detected_keys]

    return {"to_insert": to_insert, "reaffirm_ids": reaffirm_ids, "supersede_ids": supersede_ids}


# ---------- run logic (requires a live connection) ----------

def _fetch_active_observations_by_source(conn, source: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM oc_observations WHERE source = %s AND status = 'active'",
            (source,),
        )
        return cur.fetchall()


def _apply_reconciliation(conn, source: str, scan_task_id: str, plan: dict[str, Any]) -> dict[str, int]:
    for fact in plan["to_insert"]:
        observation_id = new_id()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oc_observations
                    (observation_id, source, domain, severity, confidence, status,
                     description, evidence, related_objects, recommended_action,
                     dependencies, dedup_key, scan_task_id)
                VALUES (%s, %s, %s, %s, %s, 'active', %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s)
                """,
                (
                    observation_id, source, fact["domain"], fact["severity"], fact["confidence"],
                    fact["description"], json.dumps(fact["evidence"]),
                    json.dumps(fact["related_objects"]), fact.get("recommended_action"),
                    json.dumps(fact["dependencies"]), fact["dedup_key"], scan_task_id,
                ),
            )
        log_observation_event(conn, observation_id, "observed", f"First observed by scan {scan_task_id}")

    if plan["reaffirm_ids"]:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE oc_observations SET last_seen_at = now(), updated_at = now() WHERE observation_id = ANY(%s)",
                (plan["reaffirm_ids"],),
            )
        for oid in plan["reaffirm_ids"]:
            log_observation_event(conn, oid, "reaffirmed", f"Re-observed by scan {scan_task_id}")

    if plan["supersede_ids"]:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE oc_observations SET status = 'superseded', updated_at = now() WHERE observation_id = ANY(%s)",
                (plan["supersede_ids"],),
            )
        for oid in plan["supersede_ids"]:
            log_observation_event(conn, oid, "superseded", f"No longer detected as of scan {scan_task_id}")

    return {
        "inserted": len(plan["to_insert"]),
        "reaffirmed": len(plan["reaffirm_ids"]),
        "superseded": len(plan["supersede_ids"]),
    }


def run_observation_engine(conn, task_id: str) -> dict[str, Any]:
    """The Observation Engine's scan. Registered as agent_key
    "observation_engine" in agents.py's AGENT_RUNNERS - executed through
    the existing /api/v1/agents/observation_engine/run endpoint, not a
    duplicate run path.
    """
    ensure_observation_tables(conn)

    # Deferred imports (not a circular-dependency workaround - verified
    # calyx.py and evaluation.py do not import this module or agents.py,
    # so a top-level import would work too). Kept local to this function
    # so importing observation.py to register the agent runner, or to
    # exercise the pure detect_*/reconcile functions in tests, never pulls
    # in calyx.py/evaluation.py unless a scan actually runs.
    import calyx
    import evaluation

    summary: dict[str, dict[str, int]] = {}

    decisions_rows: list[dict[str, Any]] = []
    agents_rows: list[dict[str, Any]] = []
    tasks_rows: list[dict[str, Any]] = []
    findings_rows: list[dict[str, Any]] = []

    with conn.cursor() as cur:
        for table_name, target in [
            ("oc_memory_decisions", "decisions"),
            ("oc_agent_registry", "agents"),
            ("oc_agent_tasks", "tasks"),
            ("oc_agent_findings", "findings"),
        ]:
            if _table_exists(conn, table_name):
                cur.execute(f"SELECT * FROM {table_name}")
                rows = cur.fetchall()
            else:
                rows = []
            if target == "decisions":
                decisions_rows = rows
            elif target == "agents":
                agents_rows = rows
            elif target == "tasks":
                tasks_rows = rows
            elif target == "findings":
                findings_rows = rows

    sources_and_facts: list[tuple[str, list[dict[str, Any]]]] = [
        ("engineering_memory", detect_pending_decisions(decisions_rows)),
        ("agent_registry", detect_registered_agents(agents_rows)),
        ("task_queue", detect_failed_tasks(tasks_rows)),
        ("agent_findings", detect_open_findings(findings_rows)),
        ("repository_metadata", detect_repository_metadata()),
    ]

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        db_reachable = True
    except Exception:
        db_reachable = False
    sources_and_facts.append(("health_endpoint", detect_health(db_reachable)))

    try:
        state = calyx.fetch_state(conn)
        brief = calyx.synthesize_brief(state)
        eval_result = evaluation.run_evaluation(state)
        sources_and_facts.append(("evaluation_engine", detect_domain_score_snapshots(eval_result["domain_scores"], task_id)))
        sources_and_facts.append(("mission_brief", detect_mission_brief_snapshot(brief["counts"], task_id)))
    except Exception as exc:
        summary["evaluation_engine"] = {"error": str(exc)}

    for source, facts in sources_and_facts:
        existing_active = _fetch_active_observations_by_source(conn, source)
        plan = reconcile(source, facts, existing_active)
        summary[source] = _apply_reconciliation(conn, source, task_id, plan)

    return {"scan_task_id": task_id, "sources": summary}


# ---------- scheduler hook (stub only - Phase 1 does not wire a real scheduler) ----------

def scheduled_scan_stub() -> None:
    """Intentionally not wired to anything. A real Scheduler component was
    already identified as unbuilt in the AI Fabric architecture document's
    Operational Readiness section; building one here would be exactly the
    kind of parallel infrastructure this phase is scoped to avoid. This
    function documents the intended integration point (call
    run_observation_engine via the same Agent Registry/Task Queue path a
    Scheduler would eventually trigger) without implementing scheduling
    itself. Manual "Observe Now" (POST /api/v1/agents/observation_engine/run)
    is the only trigger in Phase 1.
    """
    raise NotImplementedError(
        "Observation scheduling is not wired in Phase 1. Trigger a scan manually via "
        "POST /api/v1/agents/observation_engine/run."
    )


# ---------- API: querying observation records ----------

@router.get("/sources")
def list_sources():
    return OBSERVATION_SOURCES


@router.get("")
def list_observations(
    domain: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    try:
        with get_conn() as conn:
            ensure_observation_tables(conn)
            clauses = []
            params: list[Any] = []
            for col, val in [("domain", domain), ("source", source), ("status", status), ("severity", severity)]:
                if val:
                    clauses.append(f"{col} = %s")
                    params.append(val)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.append(limit)
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM oc_observations {where} ORDER BY last_seen_at DESC LIMIT %s", tuple(params))
                return cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List observations failed: {exc}")


@router.get("/coverage")
def observation_coverage():
    """Answers "what domains have real observations" honestly - domains
    with zero rows are reported as zero, never backfilled with a
    fabricated placeholder observation.
    """
    try:
        with get_conn() as conn:
            ensure_observation_tables(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT domain, COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE status = 'active') AS active
                    FROM oc_observations
                    GROUP BY domain
                    """
                )
                counted = {row["domain"]: row for row in cur.fetchall()}
        domains = sorted({s["domain"] for s in OBSERVATION_SOURCES})
        return [
            {
                "domain": d,
                "total_observations": counted.get(d, {}).get("total", 0),
                "active_observations": counted.get(d, {}).get("active", 0),
                "sources_available": [s["key"] for s in OBSERVATION_SOURCES if s["domain"] == d and s["available"]],
                "sources_not_yet_available": [s["key"] for s in OBSERVATION_SOURCES if s["domain"] == d and not s["available"]],
            }
            for d in domains
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Observation coverage failed: {exc}")


@router.get("/summary")
def observation_summary():
    try:
        with get_conn() as conn:
            ensure_observation_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM oc_observations WHERE status = 'active'")
                active_count = cur.fetchone()["n"]
                cur.execute("SELECT COUNT(*) AS n FROM oc_observations")
                total_count = cur.fetchone()["n"]
                cur.execute("SELECT MAX(last_seen_at) AS ts FROM oc_observations")
                last_scan = cur.fetchone()["ts"]
                cur.execute(
                    """
                    SELECT severity, COUNT(*) AS n FROM oc_observations
                    WHERE status = 'active' GROUP BY severity
                    """
                )
                by_severity = {row["severity"]: row["n"] for row in cur.fetchall()}
        return {
            "active_observations": active_count,
            "total_observations": total_count,
            "last_scan_at": last_scan,
            "active_by_severity": by_severity,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Observation summary failed: {exc}")
