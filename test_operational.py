import pytest
from fastapi import HTTPException

from operational import (
    OPERATIONAL,
    PARTIAL,
    PIPELINE_NOT_IMPLEMENTED,
    annotate_module_tables,
    build_completion_graph,
    build_operational_status,
    get_completion_graph,
    get_operational_status,
    summarize_status,
)


def test_summarize_status_counts_real_status_values():
    items = [
        {"status": OPERATIONAL},
        {"status": OPERATIONAL},
        {"status": PARTIAL},
        {"status": PIPELINE_NOT_IMPLEMENTED},
    ]
    assert summarize_status(items) == {
        OPERATIONAL: 2,
        PARTIAL: 1,
        PIPELINE_NOT_IMPLEMENTED: 1,
    }


def test_operational_status_reports_database_unreachable_honestly():
    status = build_operational_status(db_reachable=False, db_error="DATABASE_URL not set")
    assert status["database"]["reachable"] is False
    assert status["database"]["error"] == "DATABASE_URL not set"
    assert status["confidence"].startswith("high for repository-local inventory")


def test_operational_status_never_marks_missing_science_pipelines_operational():
    status = build_operational_status()
    pipelines = {item["key"]: item for item in status["science_pipelines"]}
    assert pipelines["literature"]["status"] == PIPELINE_NOT_IMPLEMENTED
    assert pipelines["pollinators"]["status"] == PIPELINE_NOT_IMPLEMENTED
    assert pipelines["mycorrhiza"]["status"] == PIPELINE_NOT_IMPLEMENTED
    assert pipelines["knowledge_graph"]["status"] == PIPELINE_NOT_IMPLEMENTED


def test_operational_status_includes_required_deployment_flags():
    status = build_operational_status()
    flags = status["deployment_required"]
    assert flags["frontend"] is False
    assert flags["backend"] is True
    assert flags["database_migration"] is False
    assert flags["render_config"] is False


def test_completion_graph_uses_canonical_mission_states_and_declared_dependency():
    graph = build_completion_graph(
        [
            {"mission_key": "CP-001", "state": "completed"},
            {"mission_key": "ATLAS-001", "state": "queued"},
            {"mission_key": "LIT-001", "state": "running"},
            {"mission_key": "CP-002", "state": "blocked"},
        ]
    )

    states = {lane["mission_key"]: lane["queue_state"] for lane in graph["lanes"]}
    assert states == {
        "CP-001": "completed",
        "ATLAS-001": "queued",
        "LIT-001": "running",
        "CP-002": "blocked",
    }
    assert graph["dependencies"] == [
        {
            "dependent": "CP-002",
            "prerequisite": "CP-001",
            "source": "CP-002 mission specification",
        }
    ]


def test_completion_graph_marks_missing_or_unrecognized_states_unknown():
    graph = build_completion_graph(
        [
            {"mission_key": "CP-001", "state": "fabricated"},
            {"mission_key": "ATLAS-001", "state": None},
        ]
    )

    assert all(lane["queue_state"] == "unknown" for lane in graph["lanes"])


def test_completion_graph_reports_missing_backend_credential_without_network(monkeypatch):
    monkeypatch.delenv("CALYX_BACKEND_API_KEY", raising=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("network must not be touched without a credential")

    monkeypatch.setattr("operational.urllib.request.urlopen", fail_if_called)

    graph = get_completion_graph()

    assert graph["available"] is False
    assert graph["reason"] == "CALYX_BACKEND_API_KEY not configured"
    assert all(lane["queue_state"] == "unknown" for lane in graph["lanes"])


def test_completion_graph_fetches_canonical_queue_with_backend_credential(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"items":[{"mission_key":"CP-001","state":"queued"}]}'

    def fake_urlopen(request, timeout):
        seen["method"] = request.get_method()
        seen["path"] = request.full_url
        seen["key"] = request.get_header("X-api-key")
        return Response()

    monkeypatch.setattr("operational.urllib.request.urlopen", fake_urlopen)

    graph = get_completion_graph()

    assert seen["method"] == "GET"
    assert seen["path"].endswith("/api/missions")
    assert seen["key"] == "configured-test-key"
    assert graph["available"] is True
    assert graph["lanes"][0]["queue_state"] == "queued"


def test_module_evidence_counts_zero_row_table_as_present(monkeypatch):
    monkeypatch.setattr("operational._file_exists", lambda path: True)
    module = {"evidence": ["memory.py"], "tables": ["decisions"]}

    result = annotate_module_tables([module], {"decisions": 0}, db_reachable=True)[0]

    assert result["completion_evidence"] == {
        "status": "evidence_present",
        "files": {"present": 1, "required": 1},
        "tables": {"present": 1, "required": 1, "checked": True},
    }


def test_module_evidence_marks_missing_table_incomplete(monkeypatch):
    monkeypatch.setattr("operational._file_exists", lambda path: True)
    module = {"evidence": ["memory.py"], "tables": ["decisions"]}

    result = annotate_module_tables([module], {"decisions": None}, db_reachable=True)[0]

    assert result["completion_evidence"]["status"] == "incomplete"


def test_module_evidence_keeps_table_state_unknown_when_database_unreachable(monkeypatch):
    monkeypatch.setattr("operational._file_exists", lambda path: True)
    module = {"evidence": ["memory.py"], "tables": ["decisions"]}

    result = annotate_module_tables([module], {}, db_reachable=False)[0]

    assert result["completion_evidence"]["status"] == "unknown"
    assert result["completion_evidence"]["tables"]["checked"] is False


def test_module_evidence_keeps_table_state_unknown_when_database_check_fails(monkeypatch):
    monkeypatch.setattr("operational._file_exists", lambda path: True)
    module = {"evidence": ["memory.py"], "tables": ["decisions"]}

    result = annotate_module_tables(
        [module],
        {"decisions": None},
        db_reachable=True,
        db_checks_complete=False,
    )[0]

    assert result["completion_evidence"]["status"] == "unknown"
    assert result["completion_evidence"]["tables"]["checked"] is False


def test_operational_status_summarizes_machine_derived_module_evidence():
    status = build_operational_status()

    assert sum(status["completion_evidence_counts"].values()) == len(status["mission_control_modules"])
    assert all("completion_evidence" in module for module in status["mission_control_modules"])


def test_operational_status_does_not_expose_internal_errors(monkeypatch):
    def fail_status(**kwargs):
        raise RuntimeError("sensitive internal error")

    monkeypatch.setattr("operational.build_operational_status", fail_status)

    with pytest.raises(HTTPException) as error:
        get_operational_status()

    assert error.value.status_code == 500
    assert error.value.detail == "Operational status failed"


def test_operational_status_sanitizes_database_errors(monkeypatch):
    def fail_connection():
        raise RuntimeError("internal database error")

    monkeypatch.setattr("operational.get_conn", fail_connection)

    status = get_operational_status()

    assert status["database"]["reachable"] is False
    assert status["database"]["error"] == "Database unavailable"
