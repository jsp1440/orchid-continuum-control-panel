# Tests for the Observation Engine's pure logic (observation.py). No
# database dependency - matches the discipline already established by
# test_calyx.py and test_evaluation.py.

from observation import (
    compute_dedup_key,
    detect_domain_score_snapshots,
    detect_failed_tasks,
    detect_health,
    detect_mission_brief_snapshot,
    detect_open_findings,
    detect_pending_decisions,
    detect_registered_agents,
    detect_repository_metadata,
    reconcile,
)


# ---------- detect_* ----------

def test_detect_pending_decisions_only_flags_proposed_and_under_review():
    decisions = [
        {"decision_id": "d1", "title": "A", "status": "proposed"},
        {"decision_id": "d2", "title": "B", "status": "under_review"},
        {"decision_id": "d3", "title": "C", "status": "implemented"},
        {"decision_id": "d4", "title": "D", "status": "rejected"},
    ]
    facts = detect_pending_decisions(decisions)
    ids = {f["evidence"]["id"] for f in facts}
    assert ids == {"d1", "d2"}


def test_detect_registered_agents_disabled_is_warning_severity():
    agents = [
        {"agent_key": "a1", "name": "A1", "enabled": True},
        {"agent_key": "a2", "name": "A2", "enabled": False},
    ]
    facts = detect_registered_agents(agents)
    severities = {f["evidence"]["id"]: f["severity"] for f in facts}
    assert severities["a1"] == "info"
    assert severities["a2"] == "warning"


def test_detect_failed_tasks_ignores_successful_runs():
    tasks = [
        {"task_id": "t1", "agent_key": "x", "status": "done", "last_error": None},
        {"task_id": "t2", "agent_key": "x", "status": "failed", "last_error": "boom"},
    ]
    facts = detect_failed_tasks(tasks)
    assert len(facts) == 1
    assert facts[0]["evidence"]["id"] == "t2"
    assert "boom" in facts[0]["description"]


def test_detect_open_findings_carries_forward_source_severity_not_reinterpreted():
    findings = [
        {"finding_id": "f1", "agent_key": "engineering_auditor", "status": "open", "severity": "critical", "summary": "bad"},
        {"finding_id": "f2", "agent_key": "engineering_auditor", "status": "resolved", "severity": "critical", "summary": "fixed"},
    ]
    facts = detect_open_findings(findings)
    assert len(facts) == 1  # resolved finding excluded
    assert facts[0]["severity"] == "critical"  # carried forward, not re-derived


def test_detect_domain_score_snapshots_low_confidence_when_score_is_null():
    domain_scores = {
        "engineering": {"label": "Engineering Health Score", "score": 92},
        "collaboration": {"label": "Collaboration Opportunity Score", "score": None},
    }
    facts = detect_domain_score_snapshots(domain_scores, "scan-1")
    by_domain = {f["domain"]: f for f in facts}
    assert by_domain["engineering"]["confidence"] == "high"
    assert by_domain["collaboration"]["confidence"] == "low"
    assert "no data available" in by_domain["collaboration"]["description"]


def test_detect_domain_score_snapshots_evidence_id_includes_scan_for_history():
    facts = detect_domain_score_snapshots({"engineering": {"label": "x", "score": 1}}, "scan-A")
    assert facts[0]["evidence"]["id"] == "engineering@scan-A"
    facts2 = detect_domain_score_snapshots({"engineering": {"label": "x", "score": 1}}, "scan-B")
    assert facts2[0]["evidence"]["id"] == "engineering@scan-B"
    assert facts[0]["evidence"]["id"] != facts2[0]["evidence"]["id"]


def test_detect_mission_brief_snapshot_cites_real_counts():
    counts = {"decisions_total": 5, "findings_open": 2, "tasks_failed": 1}
    facts = detect_mission_brief_snapshot(counts, "scan-1")
    assert "5 decisions" in facts[0]["description"]
    assert "2 open findings" in facts[0]["description"]


def test_detect_repository_metadata_honest_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    facts = detect_repository_metadata()
    assert facts[0]["evidence"]["id"] == "commit_unknown"
    assert facts[0]["confidence"] == "low"


def test_detect_repository_metadata_real_commit_when_env_var_set(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    facts = detect_repository_metadata()
    assert facts[0]["evidence"]["id"] == "commit_abc123"
    assert facts[0]["confidence"] == "high"


def test_detect_health_reachable_vs_unreachable_are_different_facts():
    reachable = detect_health(True)
    unreachable = detect_health(False)
    assert reachable[0]["evidence"]["id"] != unreachable[0]["evidence"]["id"]
    assert reachable[0]["severity"] == "info"
    assert unreachable[0]["severity"] == "critical"
    assert unreachable[0]["recommended_action"] is not None
    assert reachable[0]["recommended_action"] is None


# ---------- compute_dedup_key / reconcile ----------

def test_compute_dedup_key_is_deterministic():
    evidence = {"type": "decision", "id": "d1"}
    assert compute_dedup_key("engineering_memory", evidence) == compute_dedup_key("engineering_memory", evidence)
    assert compute_dedup_key("engineering_memory", evidence) != compute_dedup_key("task_queue", evidence)


def test_reconcile_new_fact_is_inserted_not_reaffirmed():
    facts = [{"description": "x", "domain": "engineering", "severity": "info", "confidence": "high",
              "evidence": {"type": "decision", "id": "d1"}, "related_objects": [], "recommended_action": None, "dependencies": []}]
    plan = reconcile("engineering_memory", facts, existing_active=[])
    assert len(plan["to_insert"]) == 1
    assert plan["reaffirm_ids"] == []
    assert plan["supersede_ids"] == []


def test_reconcile_existing_fact_is_reaffirmed_not_duplicated():
    facts = [{"description": "x", "domain": "engineering", "severity": "info", "confidence": "high",
              "evidence": {"type": "decision", "id": "d1"}, "related_objects": [], "recommended_action": None, "dependencies": []}]
    existing = [{"observation_id": "obs-1", "dedup_key": "engineering_memory:decision:d1"}]
    plan = reconcile("engineering_memory", facts, existing_active=existing)
    assert plan["to_insert"] == []
    assert plan["reaffirm_ids"] == ["obs-1"]
    assert plan["supersede_ids"] == []


def test_reconcile_fact_no_longer_detected_is_superseded():
    existing = [{"observation_id": "obs-1", "dedup_key": "engineering_memory:decision:d1"}]
    plan = reconcile("engineering_memory", facts=[], existing_active=existing)
    assert plan["to_insert"] == []
    assert plan["reaffirm_ids"] == []
    assert plan["supersede_ids"] == ["obs-1"]


def test_reconcile_snapshot_source_never_supersedes_old_history():
    # A snapshot source's "existing active" rows are prior scans' history -
    # they must never be superseded just because this scan's key differs.
    existing = [{"observation_id": "obs-old", "dedup_key": "evaluation_engine:domain_score_snapshot:engineering@scan-1"}]
    facts = [{"description": "x", "domain": "engineering", "severity": "info", "confidence": "high",
              "evidence": {"type": "domain_score_snapshot", "id": "engineering@scan-2"},
              "related_objects": [], "recommended_action": None, "dependencies": []}]
    plan = reconcile("evaluation_engine", facts, existing_active=existing)
    assert len(plan["to_insert"]) == 1  # new scan's snapshot inserted
    assert plan["supersede_ids"] == []  # old snapshot NOT superseded - it's permanent history


def test_reconcile_no_duplicate_insert_for_same_fact_across_repeated_calls():
    facts = [{"description": "x", "domain": "engineering", "severity": "warning", "confidence": "high",
              "evidence": {"type": "task", "id": "t1"}, "related_objects": [], "recommended_action": None, "dependencies": []}]
    plan1 = reconcile("task_queue", facts, existing_active=[])
    assert len(plan1["to_insert"]) == 1
    # Simulate the row now existing after plan1 was applied
    existing_after_insert = [{"observation_id": "obs-1", "dedup_key": compute_dedup_key("task_queue", facts[0]["evidence"])}]
    plan2 = reconcile("task_queue", facts, existing_active=existing_after_insert)
    assert plan2["to_insert"] == []  # no duplicate on second scan
    assert plan2["reaffirm_ids"] == ["obs-1"]
