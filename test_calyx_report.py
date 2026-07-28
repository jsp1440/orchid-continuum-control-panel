# Tests for Calyx Report's pure synthesis and validation logic.
#
# These tests never touch a database — they exercise the deterministic
# functions in calyx_report.py directly, matching the same pattern as
# test_calyx.py.

import pytest

from calyx_report import (
    JOURNALISM_ENGINE_AVAILABLE,
    ReportRequest,
    build_article,
    build_evidence_preview,
    validate_request,
)


# ---------- helpers ----------


def _default_req(**kwargs) -> ReportRequest:
    return ReportRequest(**kwargs)


def _evidence_with_taxonomy() -> dict:
    return build_evidence_preview(
        {
            "taxonomy_coverage": {
                "families_covered": 5,
                "species_covered": 312,
            },
            "findings": [],
        }
    )


def _evidence_no_db() -> dict:
    return build_evidence_preview(None)


# ---------- validate_request ----------


def test_validate_request_defaults_pass():
    assert validate_request(_default_req()) == []


def test_validate_request_unknown_publication():
    errors = validate_request(_default_req(publication="nonexistent_pub"))
    assert any("publication" in e for e in errors)


def test_validate_request_unknown_topic():
    errors = validate_request(_default_req(topic="bad_topic"))
    assert any("topic" in e for e in errors)


def test_validate_request_custom_topic_requires_text():
    errors = validate_request(_default_req(topic="custom", custom_topic=""))
    assert any("custom_topic" in e for e in errors)


def test_validate_request_custom_topic_with_text_passes():
    assert validate_request(_default_req(topic="custom", custom_topic="Epiphytes")) == []


def test_validate_request_bad_citation_mode():
    errors = validate_request(_default_req(citation_mode="made_up"))
    assert any("citation_mode" in e for e in errors)


def test_validate_request_bad_confidence_mode():
    errors = validate_request(_default_req(confidence_mode="made_up"))
    assert any("confidence_mode" in e for e in errors)


def test_validate_request_bad_audience():
    errors = validate_request(_default_req(audience="martians"))
    assert any("audience" in e for e in errors)


# ---------- build_evidence_preview ----------


def test_evidence_preview_no_db_returns_limited_mode():
    ev = _evidence_no_db()
    assert ev["mode"] == "limited"
    assert ev["observation_count"] is None
    assert ev["data_sources"] == []


def test_evidence_preview_with_taxonomy_coverage():
    ev = _evidence_with_taxonomy()
    assert ev["taxonomy_families_covered"] == 5
    assert ev["taxonomy_species_covered"] == 312
    assert "taxonomy_coverage" in ev["data_sources"]


def test_evidence_preview_with_open_findings():
    ev = build_evidence_preview(
        {
            "taxonomy_coverage": None,
            "findings": [
                {"status": "open", "summary": "Gap in Pleurothallid coverage"},
                {"status": "resolved", "summary": "Should not appear"},
            ],
        }
    )
    assert "Gap in Pleurothallid coverage" in ev["recent_finding_summaries"]
    assert "Should not appear" not in ev["recent_finding_summaries"]
    assert "engineering_findings" in ev["data_sources"]


def test_evidence_preview_resolved_findings_excluded():
    ev = build_evidence_preview(
        {
            "taxonomy_coverage": None,
            "findings": [
                {"status": "resolved", "summary": "Old gap"},
            ],
        }
    )
    assert ev["recent_finding_summaries"] == []
    assert "engineering_findings" not in ev["data_sources"]


def test_evidence_preview_caps_finding_summaries_at_five():
    findings = [{"status": "open", "summary": f"Gap {i}"} for i in range(10)]
    ev = build_evidence_preview({"taxonomy_coverage": None, "findings": findings})
    assert len(ev["recent_finding_summaries"]) == 5


# ---------- build_article: structure ----------


def test_build_article_returns_required_keys():
    req = _default_req()
    result = build_article(req, _evidence_no_db())
    for key in ("markdown", "word_count", "evidence_mode", "journalism_engine_available", "generated_at"):
        assert key in result, f"Missing key: {key}"


def test_build_article_word_count_is_approximate_positive():
    req = _default_req()
    result = build_article(req, _evidence_no_db())
    assert result["word_count"] > 0


def test_build_article_evidence_mode_matches_engine_flag():
    req = _default_req()
    result = build_article(req, _evidence_no_db())
    expected_mode = "full" if JOURNALISM_ENGINE_AVAILABLE else "limited"
    assert result["evidence_mode"] == expected_mode


# ---------- build_article: content constraints ----------


def test_build_article_no_fabricated_project_count(monkeypatch):
    """The article must not claim a specific project count unless it comes from evidence."""
    req = _default_req(topic="conservation_survey")
    result = build_article(req, _evidence_no_db())
    # In limited mode with no evidence, no fabricated counts should appear.
    # We check that no bare integer followed by "project" appears without citation.
    import re
    # This pattern catches things like "47 projects" or "23 active projects"
    fabrication_pattern = re.compile(r'\b\d+\s+(?:active\s+)?projects?\b', re.IGNORECASE)
    assert not fabrication_pattern.search(result["markdown"])


def test_build_article_carries_evidence_mode_disclosure():
    req = _default_req()
    result = build_article(req, _evidence_no_db())
    markdown = result["markdown"]
    # Must contain a disclosure about evidence mode (limited or full)
    assert "limited-evidence mode" in markdown or "Full Continuum" in markdown


def test_build_article_full_continuum_unavailable_shows_warning():
    req = _default_req(use_full_continuum=True)
    result = build_article(req, _evidence_no_db())
    if not JOURNALISM_ENGINE_AVAILABLE:
        assert "Journalism Engine" in result["markdown"]
        assert "not yet available" in result["markdown"] or "orchid-calyx-backend" in result["markdown"]


def test_build_article_citation_mode_none_has_no_sources_section():
    req = _default_req(citation_mode="none")
    result = build_article(req, _evidence_no_db())
    # No "Sources" heading should appear
    assert "## Sources" not in result["markdown"]


def test_build_article_source_list_mode_with_evidence_has_sources():
    req = _default_req(citation_mode="source_list")
    ev = _evidence_with_taxonomy()
    result = build_article(req, ev)
    assert "## Sources" in result["markdown"]


def test_build_article_confidence_footer_present():
    req = _default_req(confidence_mode="standard")
    result = build_article(req, _evidence_no_db())
    assert "Confidence mode:" in result["markdown"]


def test_build_article_high_confidence_mode_labelled():
    req = _default_req(confidence_mode="high_confidence_only")
    result = build_article(req, _evidence_no_db())
    assert "high-confidence" in result["markdown"].lower() or "High-confidence" in result["markdown"]


# ---------- build_article: optional sections ----------


def test_build_article_grower_action_section_included():
    req = _default_req(optional_sections=["grower_action"])
    result = build_article(req, _evidence_no_db())
    assert "## Grower Action" in result["markdown"]


def test_build_article_knowledge_gap_included():
    req = _default_req(optional_sections=["knowledge_gap"])
    result = build_article(req, _evidence_no_db())
    assert "## Knowledge Gaps" in result["markdown"]


def test_build_article_calyx_perspective_included():
    req = _default_req(optional_sections=["calyx_perspective"])
    result = build_article(req, _evidence_no_db())
    assert "## Calyx Perspective" in result["markdown"]


def test_build_article_no_optional_sections_by_default():
    req = _default_req()
    result = build_article(req, _evidence_no_db())
    assert "## Grower Action" not in result["markdown"]
    assert "## Knowledge Gaps" not in result["markdown"]
    assert "## Calyx Perspective" not in result["markdown"]


# ---------- build_article: taxonomy in body ----------


def test_build_article_conservation_survey_includes_taxonomy_count():
    req = _default_req(topic="conservation_survey")
    ev = _evidence_with_taxonomy()
    result = build_article(req, ev)
    assert "5" in result["markdown"] or "312" in result["markdown"]


def test_build_article_conservation_survey_without_taxonomy_no_fabrication():
    req = _default_req(topic="conservation_survey")
    result = build_article(req, _evidence_no_db())
    # No taxonomy numbers should appear when not in evidence
    assert "families_covered" not in result["markdown"]
    assert "species_covered" not in result["markdown"]


# ---------- build_article: custom topic ----------


def test_build_article_custom_topic_uses_provided_text():
    req = _default_req(topic="custom", custom_topic="Dracula orchids of Ecuador")
    result = build_article(req, _evidence_no_db())
    assert "Dracula orchids of Ecuador" in result["markdown"]


# ---------- build_article: publication and header ----------


def test_build_article_header_contains_publication_name():
    req = _default_req(publication="fcos_newsletter")
    result = build_article(req, _evidence_no_db())
    assert "Five Cities Orchid Society Newsletter" in result["markdown"]
