import pytest
from fastapi import HTTPException

from operational import (
    OPERATIONAL,
    PARTIAL,
    PIPELINE_NOT_IMPLEMENTED,
    annotate_module_tables,
    build_operational_status,
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
