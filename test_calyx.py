# Tests for Calyx Alpha's pure synthesis and intent-matching logic.
#
# These tests never touch a database - they exercise the deterministic
# functions in calyx.py directly against hand-built state, matching the
# separation calyx.py maintains between fetch_state() (the only DB-touching
# function) and everything else (pure).

from datetime import datetime, timedelta, timezone

from calyx import build_answer, match_intent, synthesize_brief


def _now():
    return datetime.now(timezone.utc)


def _empty_state():
    return {
        "fetched_at": _now(),
        "since": _now() - timedelta(hours=24),
        "decisions": [],
        "agents": [],
        "tasks": [],
        "findings": [],
        "outbox": [],
    }


# ---------- match_intent ----------

def test_match_intent_covers_all_example_questions():
    cases = {
        "What should we work on today?": "next_action",
        "What is blocking progress?": "blocked",
        "What should Claude build next?": "next_action",
        "What changed since yesterday?": "changed",
        "What engineering decisions are still unresolved?": "unresolved_decisions",
        "What systems are unhealthy?": "unhealthy",
        "What is closest to completion?": "closest_to_completion",
    }
    for question, expected in cases.items():
        assert match_intent(question) == expected, question


def test_match_intent_unknown_question_falls_back_to_overview():
    assert match_intent("What's the weather like on Mars?") == "overview"


def test_match_intent_is_case_insensitive():
    assert match_intent("WHAT IS BLOCKING PROGRESS?") == "blocked"


# ---------- synthesize_brief: empty state ----------

def test_synthesize_brief_empty_state_has_no_broken_or_blocked_items():
    brief = synthesize_brief(_empty_state())
    assert brief["broken"] == []
    assert brief["blocked"] == []
    assert brief["counts"]["decisions_total"] == 0
    assert brief["next_action"]["summary"].startswith("No urgent items")


def test_synthesize_brief_empty_state_reports_no_agents_as_not_all_enabled_claim():
    # With zero agents registered, the "all agents enabled" healthy signal
    # should not be fabricated - there is nothing to claim is healthy.
    brief = synthesize_brief(_empty_state())
    assert not any("agent(s) enabled" in s for s in brief["healthy"])


# ---------- synthesize_brief: realistic state ----------

def _sample_state():
    now = _now()
    since = now - timedelta(hours=24)
    return {
        "fetched_at": now,
        "since": since,
        "decisions": [
            {"decision_id": "d1", "title": "Use outbox pattern", "status": "implemented", "updated_at": now},
            {"decision_id": "d2", "title": "Adopt 7-state lifecycle", "status": "accepted", "updated_at": now},
            {"decision_id": "d3", "title": "Add Calyx", "status": "under_review", "updated_at": since - timedelta(hours=1)},
            {"decision_id": "d4", "title": "Draft idea", "status": "proposed", "updated_at": now},
        ],
        "agents": [
            {"agent_key": "engineering_auditor", "name": "Engineering Auditor", "enabled": True},
        ],
        "tasks": [
            {"task_id": "t1", "agent_key": "engineering_auditor", "status": "done", "created_at": now, "last_error": None},
            {"task_id": "t2", "agent_key": "engineering_auditor", "status": "failed", "created_at": now, "last_error": "DB timeout"},
        ],
        "findings": [
            {"finding_id": "f1", "agent_key": "engineering_auditor", "status": "open", "severity": "critical", "summary": "Critical gap", "created_at": now},
            {"finding_id": "f2", "agent_key": "engineering_auditor", "status": "open", "severity": "warning", "summary": "Minor gap", "created_at": now},
            {"finding_id": "f3", "agent_key": "engineering_auditor", "status": "resolved", "severity": "warning", "summary": "Fixed gap", "created_at": since - timedelta(hours=2)},
        ],
        "outbox": [
            {"outbox_id": "o1", "sync_status": "failed", "last_error": "connection refused", "updated_at": now},
            {"outbox_id": "o2", "sync_status": "pending", "last_error": None, "updated_at": now},
        ],
    }


def test_synthesize_brief_counts_are_correct():
    brief = synthesize_brief(_sample_state())
    counts = brief["counts"]
    assert counts["decisions_total"] == 4
    assert counts["decisions_proposed"] == 1
    assert counts["decisions_under_review"] == 1
    assert counts["decisions_accepted"] == 1
    assert counts["tasks_failed"] == 1
    assert counts["findings_open"] == 2
    assert counts["findings_critical"] == 1
    assert counts["outbox_failed"] == 1
    assert counts["agents_enabled"] == 1


def test_synthesize_brief_critical_finding_takes_priority_in_next_action():
    brief = synthesize_brief(_sample_state())
    na = brief["next_action"]
    assert na["cite"] == {"type": "finding", "id": "f1"}
    assert na["assigned_agent"] == "engineering_auditor"


def test_synthesize_brief_closest_to_completion_is_accepted_decisions_only():
    brief = synthesize_brief(_sample_state())
    ids = {i["id"] for i in brief["closest_to_completion"]}
    assert ids == {"d2"}


def test_synthesize_brief_decision_under_review_never_gets_an_assigned_agent():
    # d3 is the only under_review decision; if it were the top recommendation
    # (no critical findings/failed tasks ahead of it), it must not be
    # assigned to an agent - that requires human judgment.
    state = _sample_state()
    state["findings"] = []  # remove findings so under_review decision surfaces
    state["tasks"] = [t for t in state["tasks"] if t["status"] != "failed"]
    brief = synthesize_brief(state)
    assert brief["next_action"]["cite"] == {"type": "decision", "id": "d3"}
    assert brief["next_action"]["assigned_agent"] is None


def test_synthesize_brief_recent_changes_excludes_stale_records():
    brief = synthesize_brief(_sample_state())
    # f3 (resolved 2 hours before the 24h cutoff) must not appear in recent_changes
    ids = {i["id"] for i in brief["recent_changes"] if i["type"] == "finding"}
    assert "f3" not in ids
    assert "f1" in ids


# ---------- build_answer ----------

def test_build_answer_blocked_lists_all_blocked_items():
    brief = synthesize_brief(_sample_state())
    answer = build_answer("blocked", brief)
    assert "d3" in answer  # the under_review decision
    assert "f2" in answer  # the non-critical open finding


def test_build_answer_blocked_empty_is_reassuring_not_silent():
    brief = synthesize_brief(_empty_state())
    answer = build_answer("blocked", brief)
    assert answer == "Nothing is currently blocked."


def test_build_answer_unhealthy_cites_real_ids():
    brief = synthesize_brief(_sample_state())
    answer = build_answer("unhealthy", brief)
    assert "f1" in answer
    assert "t2" in answer
    assert "o1" in answer


def test_build_answer_overview_fallback_uses_real_counts():
    brief = synthesize_brief(_sample_state())
    answer = build_answer("overview", brief)
    assert "4 decisions" in answer
    assert "1 critical" in answer
