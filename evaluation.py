# FILE: evaluation.py
# Calyx Phase 2 - Evaluation Engine.
#
# Not an AI model. A deterministic, explainable scoring and prioritization
# layer over the state calyx.fetch_state() already reads. Every score is a
# sum of named, itemized signals, never an opaque model output - the
# "formula" field on each domain result is literally the arithmetic used.
#
# Each domain honestly reports which of its example signal types have no
# real data source yet in this repository (score: None, listed in
# not_yet_monitored) rather than fabricating a number for them. This
# mirrors the "never fabricate" principle already applied everywhere else
# in this project - a confident-looking Collaboration score built from zero
# real data would be worse than an honest null.
#
# All functions here are pure - they take already-fetched data (the same
# `state` dict calyx.fetch_state() builds, plus optional taxonomy coverage)
# and return plain dicts. Nothing in this module opens a database
# connection, which is what makes it unit-testable without a live DB.

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

# Tool vocabulary this Evaluation Engine may suggest from. Includes
# not-yet-built future systems named in the brief; they simply never get
# suggested today because no real signal maps to them yet (no vision,
# statistics, or pollination data exists in this repository).
TOOL_VOCABULARY = [
    "Claude Code", "Python", "SQL", "Literature Pipeline", "Atlas",
    "Harvesters", "Vision Lab (future)", "Statistics Engine (future)",
    "Pollination Engine (future)",
]

ENGINEERING_NOT_MONITORED = [
    "failing builds", "broken routes", "failing harvesters",
    "stale branches", "deployment failures", "missing documentation",
]
SCIENTIFIC_NOT_MONITORED = [
    "literature gaps", "pollinator gaps", "mycorrhizal gaps",
    "conservation gaps", "taxonomy inconsistencies",
]
COLLABORATION_NOT_MONITORED = [
    "inactive projects", "researchers working on similar problems",
    "conservation projects needing assistance", "collaboration opportunities",
]

STALE_REVIEW_DAYS = 7


# ---------- domain evaluators ----------

def evaluate_engineering(state: dict[str, Any]) -> dict[str, Any]:
    """Engineering Health Score: starts at 100, deducts points per real,
    itemized signal. Floored at 0. Every deduction cites the record it
    came from - resolved findings and successful runs never count against
    the score, only open/failed state does.
    """
    findings = state.get("findings", [])
    tasks = state.get("tasks", [])
    outbox = state.get("outbox", [])
    decisions = state.get("decisions", [])
    now = datetime.now(timezone.utc)

    open_findings = [f for f in findings if f.get("status") == "open"]
    critical = [f for f in open_findings if f.get("severity") == "critical"]
    warning = [f for f in open_findings if f.get("severity") != "critical"]
    failed_tasks = [t for t in tasks if t.get("status") == "failed"]
    failed_outbox = [o for o in outbox if o.get("sync_status") == "failed"]
    stale_review = [
        d for d in decisions
        if d.get("status") == "under_review" and d.get("updated_at")
        and (now - d["updated_at"]).days >= STALE_REVIEW_DAYS
    ]

    score = 100
    signals: list[dict[str, Any]] = []

    for f in critical:
        score -= 20
        signals.append({
            "type": "finding", "id": f["finding_id"], "severity": "critical", "points": -20,
            "description": f["summary"], "agent_key": f.get("agent_key"),
            "evidence": {"type": "finding", "id": f["finding_id"]},
        })
    for f in warning:
        score -= 8
        signals.append({
            "type": "finding", "id": f["finding_id"], "severity": "medium", "points": -8,
            "description": f["summary"], "agent_key": f.get("agent_key"),
            "evidence": {"type": "finding", "id": f["finding_id"]},
        })
    for t in failed_tasks:
        score -= 10
        signals.append({
            "type": "task", "id": t["task_id"], "severity": "high", "points": -10,
            "description": f"{t['agent_key']} run failed: {t.get('last_error') or 'unknown error'}",
            "agent_key": t.get("agent_key"),
            "evidence": {"type": "task", "id": t["task_id"]},
        })
    for o in failed_outbox:
        score -= 5
        signals.append({
            "type": "outbox", "id": o["outbox_id"], "severity": "medium", "points": -5,
            "description": f"Brain sync failed: {o.get('last_error') or 'unknown error'}",
            "agent_key": None,
            "evidence": {"type": "outbox", "id": o["outbox_id"]},
        })
    for d in stale_review:
        age_days = (now - d["updated_at"]).days
        score -= 5
        signals.append({
            "type": "decision", "id": d["decision_id"], "severity": "medium", "points": -5,
            "description": f"'{d['title']}' has been under_review for {age_days} days",
            "agent_key": None,
            "evidence": {"type": "decision", "id": d["decision_id"]},
        })

    score = max(0, min(100, score))

    return {
        "domain": "engineering",
        "label": "Engineering Health Score",
        "score": score,
        "score_direction": "higher_is_better",
        "formula": (
            "100, minus 20 per open critical finding, 8 per open non-critical finding, "
            "10 per failed agent task run, 5 per failed Brain Outbox sync, "
            f"5 per decision under_review for {STALE_REVIEW_DAYS}+ days; floored at 0."
        ),
        "signals": signals,
        "data_coverage": {
            "monitored": ["open findings", "failed agent task runs", "failed Brain Outbox syncs", "stale decision reviews"],
            "not_yet_monitored": ENGINEERING_NOT_MONITORED,
        },
    }


def evaluate_scientific(state: dict[str, Any]) -> dict[str, Any]:
    """Scientific Opportunity Score: higher means more real, addressable
    research opportunity exists (not a "health" score - a gap is the
    opportunity). Computed only from taxonomy/image coverage, the one
    scientific signal this repository actually has data for
    (public.orchid_taxonomy / public.images, the same tables app.py's
    existing widget endpoints already read). Every other example signal
    type in this domain (literature/pollinator/mycorrhiza/conservation
    gaps, taxonomy inconsistencies) has no data source anywhere in this
    repository and is reported as such, not estimated.
    """
    coverage = state.get("taxonomy_coverage")
    if not coverage or not coverage.get("total_taxa"):
        return {
            "domain": "scientific",
            "label": "Scientific Opportunity Score",
            "score": None,
            "score_direction": "higher_is_better (more gap = more research opportunity)",
            "formula": "No score computed - no taxonomy/image coverage data available in this database.",
            "signals": [],
            "data_coverage": {"monitored": [], "not_yet_monitored": SCIENTIFIC_NOT_MONITORED + ["Atlas completeness"]},
        }

    total = coverage["total_taxa"]
    without_images = coverage.get("taxa_without_images") or 0
    gap_pct = round(100 * without_images / total) if total else 0

    signals: list[dict[str, Any]] = []
    if without_images:
        signals.append({
            "type": "taxonomy_gap", "id": "image_coverage", "severity": "opportunity", "points": None,
            "description": f"{without_images} of {total} taxa ({gap_pct}%) have no linked images",
            "agent_key": None,
            "evidence": {"type": "taxonomy_coverage", "id": "image_coverage"},
        })

    return {
        "domain": "scientific",
        "label": "Scientific Opportunity Score",
        "score": gap_pct,
        "score_direction": "higher_is_better (more gap = more research opportunity)",
        "formula": "percentage of taxa in orchid_taxonomy with zero linked rows in images.",
        "signals": signals,
        "data_coverage": {
            "monitored": ["taxonomy/image coverage"],
            "not_yet_monitored": SCIENTIFIC_NOT_MONITORED + ["Atlas completeness (beyond image coverage)"],
        },
    }


def evaluate_mission_progress(state: dict[str, Any]) -> dict[str, Any]:
    """Mission Progress Score: percentage of all Engineering Memory
    decisions ever recorded that have reached a settled state (implemented
    or superseded). Surfaces accepted-but-not-implemented decisions as
    completion opportunities and under_review decisions as blockers -
    reusing the exact same lifecycle data Engineering Memory already owns,
    not a second progress-tracking system.
    """
    decisions = state.get("decisions", [])
    total = len(decisions)
    settled = [d for d in decisions if d.get("status") in ("implemented", "superseded")]
    accepted = [d for d in decisions if d.get("status") == "accepted"]
    under_review = [d for d in decisions if d.get("status") == "under_review"]

    score = round(100 * len(settled) / total) if total else None

    signals: list[dict[str, Any]] = []
    for d in accepted:
        signals.append({
            "type": "decision", "id": d["decision_id"], "severity": "opportunity", "points": None,
            "description": f"'{d['title']}' is accepted and closest to completion - needs implementation",
            "agent_key": None,
            "evidence": {"type": "decision", "id": d["decision_id"]},
        })
    for d in under_review:
        signals.append({
            "type": "decision", "id": d["decision_id"], "severity": "blocker", "points": None,
            "description": f"'{d['title']}' is blocked on human review",
            "agent_key": None,
            "evidence": {"type": "decision", "id": d["decision_id"]},
        })

    return {
        "domain": "mission_progress",
        "label": "Mission Progress Score",
        "score": score,
        "score_direction": "higher_is_better",
        "formula": (
            "percentage of Engineering Memory decisions with status implemented or superseded, "
            "out of all decisions ever recorded; null if none exist yet."
        ),
        "signals": signals,
        "data_coverage": {
            "monitored": ["Engineering Memory decision lifecycle status"],
            "not_yet_monitored": ["project-tracking systems outside Engineering Memory"],
        },
    }


def evaluate_collaboration(_state: dict[str, Any]) -> dict[str, Any]:
    """Collaboration Opportunity Score: always null today. No researcher,
    project, or collaboration-tracking table exists anywhere in this
    repository - this function exists so the domain is evaluated
    independently as required, but it has nothing real to score yet, and
    says so rather than inventing a plausible-looking number.
    """
    return {
        "domain": "collaboration",
        "label": "Collaboration Opportunity Score",
        "score": None,
        "score_direction": "higher_is_better",
        "formula": "No score computed - no researcher, project, or collaboration data source exists in this repository yet.",
        "signals": [],
        "data_coverage": {"monitored": [], "not_yet_monitored": COLLABORATION_NOT_MONITORED},
    }


def evaluate_all(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        evaluate_engineering(state),
        evaluate_scientific(state),
        evaluate_mission_progress(state),
        evaluate_collaboration(state),
    ]


# ---------- priority ranking ----------

DOMAIN_WEIGHT = {"engineering": 3, "mission_progress": 2, "scientific": 1, "collaboration": 1}
SEVERITY_WEIGHT = {"critical": 100, "high": 80, "blocker": 70, "medium": 50, "opportunity": 20}


def _priority_level(severity: str) -> str:
    if severity == "critical":
        return "critical"
    if severity in ("high", "blocker"):
        return "high"
    if severity == "medium":
        return "medium"
    return "low"


def _suggested_tool(domain: str, signal_type: str, severity: str) -> Optional[str]:
    """Deterministic, explainable mapping - never a hardcoded AI provider.
    Returns None (not a fabricated guess) for anything that genuinely
    requires human judgment rather than a tool.
    """
    if signal_type == "finding" and domain == "engineering":
        return "Claude Code"
    if signal_type == "task":
        return "Claude Code"
    if signal_type == "outbox":
        return "SQL"
    if signal_type == "decision" and severity == "blocker":
        return None  # requires human review, not a tool
    if signal_type == "decision" and severity == "opportunity":
        return "Claude Code"
    if signal_type == "taxonomy_gap":
        return "Harvesters"
    return None


def _confidence(signal_type: str) -> str:
    """"high" for anything citing a real row Calyx read directly;
    "medium" for aggregated/derived heuristics (e.g. a coverage
    percentage computed across many rows, not one record).
    """
    if signal_type == "taxonomy_gap":
        return "medium"
    return "high"


def _expected_impact(domain: str, severity: str) -> str:
    if domain == "engineering" and severity == "critical":
        return "Prevents further engineering drift and protects institutional memory integrity."
    if domain == "engineering" and severity == "high":
        return "Restores a broken automated process (an agent run or a Brain sync)."
    if domain == "engineering" and severity == "medium":
        return "Clears a minor engineering issue before it compounds."
    if domain == "mission_progress" and severity == "opportunity":
        return "Moves an already-accepted decision to completion, reducing open institutional work."
    if domain == "mission_progress" and severity == "blocker":
        return "Unblocks a decision awaiting human review, which may itself be blocking dependent work."
    if domain == "scientific":
        return "Improves taxonomy/image documentation coverage, a foundational research asset."
    return "Addresses an open item of lower immediate severity."


def _dependencies_for_decision(decision_id: str, relationships: list[dict[str, Any]]) -> list[str]:
    """A decision's dependencies are the decisions it's recorded as a
    parent_of child of - i.e. other decisions pointing to it via
    parent_of. Returns real decision_ids only; empty list (not a guess)
    when no relationship data says otherwise.
    """
    return [
        r["from_decision_id"] for r in relationships
        if r.get("to_decision_id") == decision_id and r.get("relationship_type") == "parent_of"
    ]


def build_priorities(domain_results: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combines every domain's signals into one ranked list. Ranking is a
    simple, inspectable weight: severity tier x domain weight - not a
    black box. Every item is traceable back to the exact record it cites.
    """
    scored_items: list[tuple[float, dict[str, Any]]] = []

    for domain_result in domain_results:
        domain = domain_result["domain"]
        for signal in domain_result["signals"]:
            severity = signal["severity"]
            weight = SEVERITY_WEIGHT.get(severity, 10) * DOMAIN_WEIGHT.get(domain, 1)

            dependencies: list[str] = []
            if signal["type"] == "decision":
                dependencies = _dependencies_for_decision(signal["id"], relationships)

            item = {
                "domain": domain,
                "priority": _priority_level(severity),
                "reason": signal["description"],
                "evidence": signal["evidence"],
                "expected_impact": _expected_impact(domain, severity),
                "dependencies": dependencies,
                "suggested_agent": signal.get("agent_key"),
                "suggested_tool": _suggested_tool(domain, signal["type"], severity),
                "confidence": _confidence(signal["type"]),
            }
            scored_items.append((weight, item))

    scored_items.sort(key=lambda pair: -pair[0])
    priorities = []
    for rank, (_weight, item) in enumerate(scored_items, start=1):
        item["rank"] = rank
        priorities.append(item)
    return priorities


def run_evaluation(state: dict[str, Any]) -> dict[str, Any]:
    domain_results = evaluate_all(state)
    relationships = state.get("relationships", [])
    priorities = build_priorities(domain_results, relationships)
    return {
        "domain_scores": {d["domain"]: d for d in domain_results},
        "priorities": priorities,
        "tool_vocabulary": TOOL_VOCABULARY,
    }
