# Tests for the Evaluation Engine (evaluation.py). All pure - no database
# dependency, matching the same discipline as test_calyx.py.

from datetime import datetime, timedelta, timezone

from evaluation import (
    build_priorities,
    evaluate_collaboration,
    evaluate_engineering,
    evaluate_mission_progress,
    evaluate_scientific,
    run_evaluation,
)


def _now():
    return datetime.now(timezone.utc)


def _empty_state():
    return {
        "decisions": [], "relationships": [], "agents": [], "tasks": [],
        "findings": [], "outbox": [], "taxonomy_coverage": None,
    }


# ---------- evaluate_engineering ----------

def test_engineering_empty_state_scores_100():
    result = evaluate_engineering(_empty_state())
    assert result["score"] == 100
    assert result["signals"] == []
    assert result["domain"] == "engineering"


def test_engineering_deducts_for_critical_finding_only_not_resolved():
    state = _empty_state()
    state["findings"] = [
        {"finding_id": "f1", "agent_key": "engineering_auditor", "status": "open", "severity": "critical", "summary": "bad"},
        {"finding_id": "f2", "agent_key": "engineering_auditor", "status": "resolved", "severity": "critical", "summary": "old, fixed"},
    ]
    result = evaluate_engineering(state)
    assert result["score"] == 80  # only f1 counts; f2 is resolved
    assert len(result["signals"]) == 1
    assert result["signals"][0]["id"] == "f1"


def test_engineering_score_never_goes_below_zero():
    state = _empty_state()
    state["findings"] = [
        {"finding_id": f"f{i}", "agent_key": "x", "status": "open", "severity": "critical", "summary": "bad"}
        for i in range(10)
    ]
    result = evaluate_engineering(state)
    assert result["score"] == 0


def test_engineering_reports_not_yet_monitored_signal_types():
    result = evaluate_engineering(_empty_state())
    assert "failing builds" in result["data_coverage"]["not_yet_monitored"]
    assert "stale branches" in result["data_coverage"]["not_yet_monitored"]


def test_engineering_stale_review_deduction():
    state = _empty_state()
    old = _now() - timedelta(days=10)
    recent = _now() - timedelta(days=1)
    state["decisions"] = [
        {"decision_id": "d1", "title": "Stale one", "status": "under_review", "updated_at": old},
        {"decision_id": "d2", "title": "Fresh one", "status": "under_review", "updated_at": recent},
    ]
    result = evaluate_engineering(state)
    assert result["score"] == 95  # only d1 (10 days old) counts
    ids = {s["id"] for s in result["signals"]}
    assert "d1" in ids
    assert "d2" not in ids


# ---------- evaluate_scientific ----------

def test_scientific_no_data_returns_null_score():
    result = evaluate_scientific(_empty_state())
    assert result["score"] is None
    assert result["signals"] == []
    assert "literature gaps" in result["data_coverage"]["not_yet_monitored"]


def test_scientific_with_coverage_data_computes_gap_percentage():
    state = _empty_state()
    state["taxonomy_coverage"] = {"total_taxa": 100, "taxa_without_images": 25}
    result = evaluate_scientific(state)
    assert result["score"] == 25
    assert len(result["signals"]) == 1
    assert "25 of 100" in result["signals"][0]["description"]


def test_scientific_full_coverage_produces_no_signal():
    state = _empty_state()
    state["taxonomy_coverage"] = {"total_taxa": 50, "taxa_without_images": 0}
    result = evaluate_scientific(state)
    assert result["score"] == 0
    assert result["signals"] == []


# ---------- evaluate_mission_progress ----------

def test_mission_progress_no_decisions_returns_null_score():
    result = evaluate_mission_progress(_empty_state())
    assert result["score"] is None


def test_mission_progress_settled_percentage():
    state = _empty_state()
    state["decisions"] = [
        {"decision_id": "d1", "title": "A", "status": "implemented"},
        {"decision_id": "d2", "title": "B", "status": "superseded"},
        {"decision_id": "d3", "title": "C", "status": "proposed"},
        {"decision_id": "d4", "title": "D", "status": "under_review"},
    ]
    result = evaluate_mission_progress(state)
    assert result["score"] == 50  # 2 of 4 settled


def test_mission_progress_surfaces_accepted_as_opportunity_and_under_review_as_blocker():
    state = _empty_state()
    state["decisions"] = [
        {"decision_id": "d1", "title": "Accepted one", "status": "accepted"},
        {"decision_id": "d2", "title": "Reviewing one", "status": "under_review"},
    ]
    result = evaluate_mission_progress(state)
    severities = {s["id"]: s["severity"] for s in result["signals"]}
    assert severities["d1"] == "opportunity"
    assert severities["d2"] == "blocker"


# ---------- evaluate_collaboration ----------

def test_collaboration_always_null_never_fabricated():
    result = evaluate_collaboration(_empty_state())
    assert result["score"] is None
    assert result["signals"] == []
    assert len(result["data_coverage"]["not_yet_monitored"]) == 4


# ---------- build_priorities ----------

def test_build_priorities_ranks_critical_engineering_above_scientific_opportunity():
    state = _empty_state()
    state["findings"] = [{"finding_id": "f1", "agent_key": "x", "status": "open", "severity": "critical", "summary": "urgent"}]
    state["taxonomy_coverage"] = {"total_taxa": 10, "taxa_without_images": 5}
    result = run_evaluation(state)
    priorities = result["priorities"]
    assert priorities[0]["domain"] == "engineering"
    assert priorities[0]["priority"] == "critical"
    assert any(p["domain"] == "scientific" for p in priorities)
    # scientific opportunity must rank below the critical engineering item
    sci_rank = next(p["rank"] for p in priorities if p["domain"] == "scientific")
    assert sci_rank > priorities[0]["rank"]


def test_build_priorities_decision_dependencies_from_parent_of_relationships():
    state = _empty_state()
    state["decisions"] = [{"decision_id": "child", "title": "Child decision", "status": "accepted"}]
    state["relationships"] = [
        {"from_decision_id": "parent1", "to_decision_id": "child", "relationship_type": "parent_of"},
        {"from_decision_id": "unrelated", "to_decision_id": "child", "relationship_type": "conflicts_with"},
    ]
    result = run_evaluation(state)
    item = next(p for p in result["priorities"] if p["evidence"]["id"] == "child")
    assert item["dependencies"] == ["parent1"]  # only parent_of counted, not conflicts_with


def test_build_priorities_never_assigns_agent_to_human_review_blocker():
    state = _empty_state()
    state["decisions"] = [{"decision_id": "d1", "title": "Needs review", "status": "under_review", "updated_at": _now()}]
    result = run_evaluation(state)
    item = next(p for p in result["priorities"] if p["evidence"]["id"] == "d1")
    assert item["suggested_agent"] is None
    assert item["suggested_tool"] is None


def test_build_priorities_assigns_real_agent_key_from_finding():
    state = _empty_state()
    state["findings"] = [{"finding_id": "f1", "agent_key": "engineering_auditor", "status": "open", "severity": "critical", "summary": "x"}]
    result = run_evaluation(state)
    item = result["priorities"][0]
    assert item["suggested_agent"] == "engineering_auditor"


def test_run_evaluation_empty_state_has_no_priorities():
    result = run_evaluation(_empty_state())
    assert result["priorities"] == []
    assert set(result["domain_scores"].keys()) == {"engineering", "scientific", "mission_progress", "collaboration"}
