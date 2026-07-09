from operational import (
    OPERATIONAL,
    PARTIAL,
    PIPELINE_NOT_IMPLEMENTED,
    build_operational_status,
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
