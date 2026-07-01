# FILE: calyx.py
# Calyx Alpha - the first operational version of Calyx.
#
# Calyx is not another entry in the Agent Registry - it is the directing
# intelligence that reads across Engineering Memory, the Task Queue, the
# Agent Registry, Engineering Findings, and the Brain Outbox to produce a
# Mission Brief and answer grounded questions about institutional state.
#
# Phase Alpha is deliberately read-only: Calyx observes, analyzes,
# recommends, and answers questions - it never writes to any table, never
# executes code, never deploys, and never approves its own work. There is
# no Model Router or LLM call in this phase (the AI Fabric's Model Router
# does not exist yet); every answer is synthesized from real query results
# through deterministic templates, not invented text and not a
# general-purpose chat model. Decision-proposal automation (turning a
# significant recommendation into a draft Engineering Memory decision) is
# explicitly deferred to a later phase - Alpha only observes and reports.

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel

from admin import require_admin_token

router = APIRouter(
    prefix="/api/v1/calyx",
    tags=["Calyx"],
    dependencies=[Depends(require_admin_token)],
)

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


# ---------- data fetch (requires a live connection) ----------

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


def fetch_state(conn) -> dict[str, Any]:
    """Reads Engineering Memory, Task Queue, Agent Registry, Findings, and
    Brain Outbox. Returns raw rows only - no synthesis happens here, so
    this function is the only part of Calyx that touches the database.
    Tables that don't exist yet (e.g. a fresh database where no agent has
    ever run) are treated as empty, not an error - Calyx does not own or
    create any of these tables.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    state: dict[str, Any] = {"fetched_at": datetime.now(timezone.utc), "since": since}

    tables = [
        ("oc_memory_decisions", "decisions", "updated_at"),
        ("oc_agent_registry", "agents", "agent_key"),
        ("oc_agent_tasks", "tasks", "created_at"),
        ("oc_agent_findings", "findings", "created_at"),
        ("oc_memory_outbox", "outbox", "updated_at"),
    ]
    with conn.cursor() as cur:
        for table_name, key, order_col in tables:
            if _table_exists(conn, table_name):
                cur.execute(f"SELECT * FROM {table_name} ORDER BY {order_col} DESC LIMIT 500")
                state[key] = cur.fetchall()
            else:
                state[key] = []

    return state


# ---------- pure synthesis (no DB access - fully unit-testable) ----------

def _by_status(rows: list[dict[str, Any]], field: str, status: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get(field) == status]


def _recent(rows: list[dict[str, Any]], since: datetime, key: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        ts = r.get(key)
        if ts is not None and ts >= since:
            out.append(r)
    return out


def _recommend_next_action(
    critical_findings: list[dict[str, Any]],
    failed_tasks: list[dict[str, Any]],
    under_review: list[dict[str, Any]],
    open_findings: list[dict[str, Any]],
    proposed: list[dict[str, Any]],
) -> dict[str, Any]:
    """Priority order: critical findings > failed runs > decisions awaiting
    human review > other open findings > proposed decisions > nothing urgent.
    Agent assignment is only suggested when a real agent_key is attached to
    the underlying record - Calyx never invents a plausible-sounding agent
    for work no registered agent actually does.
    """
    if critical_findings:
        f = critical_findings[0]
        return {
            "summary": f"Resolve critical finding: {f['summary']}",
            "cite": {"type": "finding", "id": f["finding_id"]},
            "assigned_agent": f.get("agent_key"),
            "reason": "Critical findings take priority over all other work.",
        }
    if failed_tasks:
        t = failed_tasks[0]
        return {
            "summary": f"Investigate failed {t['agent_key']} run (task {t['task_id']}): {t.get('last_error') or 'unknown error'}",
            "cite": {"type": "task", "id": t["task_id"]},
            "assigned_agent": t.get("agent_key"),
            "reason": "A failed agent run may indicate a broken integration.",
        }
    if under_review:
        d = under_review[0]
        return {
            "summary": f"Review decision '{d['title']}' (currently under_review)",
            "cite": {"type": "decision", "id": d["decision_id"]},
            "assigned_agent": None,
            "reason": "Decisions under review require human judgment - no agent is assigned.",
        }
    if open_findings:
        f = open_findings[0]
        return {
            "summary": f"Review finding: {f['summary']}",
            "cite": {"type": "finding", "id": f["finding_id"]},
            "assigned_agent": f.get("agent_key"),
            "reason": "Open findings should be acknowledged or resolved.",
        }
    if proposed:
        d = proposed[0]
        return {
            "summary": f"Move '{d['title']}' into review (currently proposed)",
            "cite": {"type": "decision", "id": d["decision_id"]},
            "assigned_agent": None,
            "reason": "Proposed decisions need a reviewer before they can advance.",
        }
    return {
        "summary": "No urgent items. Consider proposing a new Engineering Memory decision or running an agent proactively.",
        "cite": None,
        "assigned_agent": None,
        "reason": "All tracked signals are currently clear.",
    }


def synthesize_brief(state: dict[str, Any]) -> dict[str, Any]:
    decisions = state.get("decisions", [])
    agents = state.get("agents", [])
    tasks = state.get("tasks", [])
    findings = state.get("findings", [])
    outbox = state.get("outbox", [])
    since = state.get("since")

    proposed = _by_status(decisions, "status", "proposed")
    under_review = _by_status(decisions, "status", "under_review")
    accepted = _by_status(decisions, "status", "accepted")

    failed_tasks = _by_status(tasks, "status", "failed")
    running_tasks = [t for t in tasks if t.get("status") in ("pending", "running")]

    open_findings = _by_status(findings, "status", "open")
    critical_findings = [f for f in open_findings if f.get("severity") == "critical"]
    non_critical_open = [f for f in open_findings if f.get("severity") != "critical"]

    failed_outbox = _by_status(outbox, "sync_status", "failed")
    pending_outbox = _by_status(outbox, "sync_status", "pending")

    disabled_agents = [a for a in agents if not a.get("enabled")]

    healthy: list[str] = []
    if not critical_findings:
        healthy.append("No critical findings open.")
    if not failed_tasks:
        healthy.append("No failed agent task runs.")
    if not failed_outbox:
        healthy.append("No failed Brain Outbox sync attempts.")
    if agents and not disabled_agents:
        healthy.append(f"All {len(agents)} registered agent(s) enabled.")

    broken: list[dict[str, Any]] = []
    for f in critical_findings:
        broken.append({"type": "finding", "id": f["finding_id"], "summary": f["summary"]})
    for t in failed_tasks[:10]:
        broken.append({
            "type": "task", "id": t["task_id"],
            "summary": f"{t['agent_key']} run failed: {t.get('last_error') or 'unknown error'}",
        })
    for o in failed_outbox[:10]:
        broken.append({
            "type": "outbox", "id": o["outbox_id"],
            "summary": f"Brain sync failed: {o.get('last_error') or 'unknown error'}",
        })

    blocked: list[dict[str, Any]] = []
    for f in non_critical_open:
        blocked.append({"type": "finding", "id": f["finding_id"], "summary": f["summary"], "severity": f["severity"]})
    for d in under_review:
        blocked.append({"type": "decision", "id": d["decision_id"], "summary": f"'{d['title']}' awaiting review decision"})

    waiting_for_review: list[dict[str, Any]] = []
    for d in proposed + under_review:
        waiting_for_review.append({"type": "decision", "id": d["decision_id"], "summary": d["title"], "status": d["status"]})
    for f in open_findings:
        waiting_for_review.append({"type": "finding", "id": f["finding_id"], "summary": f["summary"]})

    closest_to_completion: list[dict[str, Any]] = [
        {"type": "decision", "id": d["decision_id"], "summary": d["title"], "updated_at": d.get("updated_at")}
        for d in accepted
    ]

    recent_changes: list[dict[str, Any]] = []
    if since is not None:
        for d in _recent(decisions, since, "updated_at"):
            recent_changes.append({"type": "decision", "id": d["decision_id"], "summary": f"'{d['title']}' -> {d['status']}"})
        for t in _recent(tasks, since, "created_at"):
            recent_changes.append({"type": "task", "id": t["task_id"], "summary": f"{t['agent_key']} run {t['status']}"})
        for f in _recent(findings, since, "created_at"):
            recent_changes.append({"type": "finding", "id": f["finding_id"], "summary": f["summary"]})
        for o in _recent(outbox, since, "updated_at"):
            recent_changes.append({"type": "outbox", "id": o["outbox_id"], "summary": f"sync {o['sync_status']}"})

    next_action = _recommend_next_action(critical_findings, failed_tasks, under_review, open_findings, proposed)

    return {
        "healthy": healthy,
        "broken": broken,
        "blocked": blocked,
        "waiting_for_review": waiting_for_review,
        "closest_to_completion": closest_to_completion,
        "recent_changes": recent_changes,
        "next_action": next_action,
        "counts": {
            "decisions_total": len(decisions),
            "decisions_proposed": len(proposed),
            "decisions_under_review": len(under_review),
            "decisions_accepted": len(accepted),
            "tasks_failed": len(failed_tasks),
            "tasks_active": len(running_tasks),
            "findings_open": len(open_findings),
            "findings_critical": len(critical_findings),
            "outbox_pending": len(pending_outbox),
            "outbox_failed": len(failed_outbox),
            "agents_enabled": len(agents) - len(disabled_agents),
            "agents_total": len(agents),
        },
    }


# ---------- conversation: intent matching + answer templating (pure) ----------

INTENT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("next_action", ("build next", "work on today", "should we work on", "priorities", "today's priorities")),
    ("blocked", ("blocking progress", "is blocking", "what's blocking", "what is blocked")),
    ("changed", ("changed since yesterday", "what changed", "since yesterday")),
    ("unresolved_decisions", ("still unresolved", "unresolved decisions", "decisions are still")),
    ("unhealthy", ("unhealthy", "is broken", "what's broken", "systems are unhealthy")),
    ("closest_to_completion", ("closest to completion", "almost done", "nearly done")),
    ("healthy", ("is healthy", "what's healthy")),
]


def match_intent(question: str) -> str:
    q = (question or "").strip().lower()
    for intent, phrases in INTENT_PATTERNS:
        for phrase in phrases:
            if phrase in q:
                return intent
    return "overview"


def _format_items(items: list[dict[str, Any]]) -> str:
    return "\n".join(f"- [{i['type']}:{i['id']}] {i['summary']}" for i in items)


def build_answer(intent: str, brief: dict[str, Any]) -> str:
    if intent == "next_action":
        na = brief["next_action"]
        agent_note = f" (agent: {na['assigned_agent']})" if na.get("assigned_agent") else " (requires human review - no agent assigned)"
        return f"{na['summary']}{agent_note} — {na['reason']}"

    if intent == "blocked":
        items = brief["blocked"]
        if not items:
            return "Nothing is currently blocked."
        return f"{len(items)} item(s) need attention before progress can continue:\n" + _format_items(items)

    if intent == "changed":
        items = brief["recent_changes"]
        if not items:
            return "No recorded changes in the last 24 hours."
        return f"{len(items)} change(s) in the last 24 hours:\n" + _format_items(items)

    if intent == "unresolved_decisions":
        items = [i for i in brief["waiting_for_review"] if i["type"] == "decision"]
        if not items:
            return "No unresolved engineering decisions."
        lines = "\n".join(f"- [{i['id']}] {i['summary']} ({i['status']})" for i in items)
        return f"{len(items)} unresolved decision(s):\n{lines}"

    if intent == "unhealthy":
        items = brief["broken"]
        if not items:
            return "No unhealthy systems detected."
        return f"{len(items)} issue(s) detected:\n" + _format_items(items)

    if intent == "closest_to_completion":
        items = brief["closest_to_completion"]
        if not items:
            return "No decisions are currently accepted-but-not-yet-implemented."
        lines = "\n".join(f"- [{i['id']}] {i['summary']}" for i in items)
        return f"{len(items)} decision(s) accepted and awaiting implementation:\n{lines}"

    if intent == "healthy":
        items = brief["healthy"]
        if not items:
            return "No positive health signals recorded yet."
        return "\n".join(f"- {s}" for s in items)

    c = brief["counts"]
    return (
        "I don't have a specific answer pattern for that yet. Here's the current overview: "
        f"{c['decisions_total']} decisions ({c['decisions_proposed']} proposed, {c['decisions_under_review']} under review, "
        f"{c['decisions_accepted']} accepted), {c['findings_open']} open findings ({c['findings_critical']} critical), "
        f"{c['tasks_failed']} failed task runs, {c['outbox_failed']} failed Brain syncs, "
        f"{c['agents_enabled']}/{c['agents_total']} agents enabled."
    )


# ---------- API ----------

class AskRequest(BaseModel):
    question: str


@router.get("/mission-brief")
def get_mission_brief():
    try:
        with get_conn() as conn:
            state = fetch_state(conn)
        brief = synthesize_brief(state)
        brief["generated_at"] = state["fetched_at"]
        return brief
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Mission brief generation failed: {exc}")


@router.post("/ask")
def ask(payload: AskRequest):
    try:
        with get_conn() as conn:
            state = fetch_state(conn)
        brief = synthesize_brief(state)
        intent = match_intent(payload.question)
        answer = build_answer(intent, brief)
        return {"question": payload.question, "matched_intent": intent, "answer": answer}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ask failed: {exc}")
