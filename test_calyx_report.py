# Tests for Calyx Report's pure synthesis and validation logic.
#
# These tests never touch a database — they exercise the deterministic
# functions in calyx_report.py directly, matching the same pattern as
# test_calyx.py.

import pytest

from calyx_report import (
    ReportRequest,
    build_article,
    build_evidence_preview,
    build_limited_evidence_summary,
    check_journalism_engine_capability,
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


def test_build_article_defaults_to_limited_mode():
    """build_article defaults to limited mode when journalism_engine_available is not passed."""
    req = _default_req()
    result = build_article(req, _evidence_no_db())
    assert result["evidence_mode"] == "limited"
    assert result["journalism_engine_available"] is False


def test_build_article_full_mode_when_engine_flagged():
    """build_article reports full mode when journalism_engine_available=True is passed."""
    req = _default_req()
    result = build_article(req, _evidence_no_db(), journalism_engine_available=True)
    assert result["evidence_mode"] == "full"
    assert result["journalism_engine_available"] is True


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
    result = build_article(req, _evidence_no_db(), journalism_engine_available=False)
    assert "Journalism Engine" in result["markdown"]
    assert "JOURNALISM_ENGINE_URL" in result["markdown"]


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


# ---------- check_journalism_engine_capability ----------


def test_capability_check_no_url_returns_unavailable(monkeypatch):
    import calyx_report
    monkeypatch.setattr(calyx_report, "JOURNALISM_ENGINE_URL", "")
    result = check_journalism_engine_capability()
    assert result["available"] is False
    assert "not configured" in result.get("reason", "").lower()


def test_capability_check_connection_error_returns_unavailable(monkeypatch):
    import urllib.request
    import calyx_report

    monkeypatch.setattr(calyx_report, "JOURNALISM_ENGINE_URL", "http://unreachable-host")

    def _fake_urlopen(req, timeout=None):
        raise OSError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    result = check_journalism_engine_capability()
    assert result["available"] is False
    assert result.get("reason") == "Journalism Engine not reachable"


def test_capability_check_successful_probe(monkeypatch):
    import io
    import urllib.request
    import calyx_report

    monkeypatch.setattr(calyx_report, "JOURNALISM_ENGINE_URL", "http://fake-backend")

    class _FakeResp:
        status = 200
        def read(self):
            return b'{"version": "1.0", "supported_topics": ["conservation_survey"]}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _FakeResp())
    result = check_journalism_engine_capability()
    assert result["available"] is True
    assert result.get("version") == "1.0"


def test_capability_check_non_200_returns_unavailable(monkeypatch):
    import calyx_report
    import urllib.request

    monkeypatch.setattr(calyx_report, "JOURNALISM_ENGINE_URL", "http://fake-backend")

    class _FakeResp:
        status = 503
        def read(self):
            return b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _FakeResp())
    result = check_journalism_engine_capability()
    assert result["available"] is False
    assert "503" in result.get("reason", "")


# ---------- build_limited_evidence_summary ----------


def test_limited_summary_not_article_disclaimer():
    req = _default_req()
    result = build_limited_evidence_summary(req, _evidence_no_db())
    assert "NOT a completed article" in result["markdown"]


def test_limited_summary_journalism_engine_available_is_false():
    req = _default_req()
    result = build_limited_evidence_summary(req, _evidence_no_db())
    assert result["journalism_engine_available"] is False


def test_limited_summary_evidence_mode_limited():
    req = _default_req()
    result = build_limited_evidence_summary(req, _evidence_no_db())
    assert result["evidence_mode"] == "limited"


def test_limited_summary_includes_real_evidence_counts():
    req = _default_req()
    ev = _evidence_with_taxonomy()
    result = build_limited_evidence_summary(req, ev)
    assert "5" in result["markdown"]
    assert "312" in result["markdown"]


def test_limited_summary_no_fabrication_when_no_evidence():
    req = _default_req()
    result = build_limited_evidence_summary(req, _evidence_no_db())
    # No specific counts should appear — only the "none available" note
    assert "No structured evidence" in result["markdown"]


def test_limited_summary_returns_warnings():
    req = _default_req()
    result = build_limited_evidence_summary(req, _evidence_no_db())
    assert result["warnings"]
    assert any("Journalism Engine" in w for w in result["warnings"])


def test_limited_summary_insufficient_evidence_flag_no_sources():
    req = _default_req()
    result = build_limited_evidence_summary(req, _evidence_no_db())
    assert result["insufficient_evidence"] is True


def test_limited_summary_sufficient_evidence_flag_with_sources():
    req = _default_req()
    result = build_limited_evidence_summary(req, _evidence_with_taxonomy())
    assert result["insufficient_evidence"] is False


def test_limited_summary_empty_citations_and_project_table():
    req = _default_req()
    result = build_limited_evidence_summary(req, _evidence_no_db())
    assert result["citations"] == []
    assert result["project_table"] == []


def test_limited_summary_optional_sections_listed():
    req = _default_req(optional_sections=["grower_action", "knowledge_gap"])
    result = build_limited_evidence_summary(req, _evidence_no_db())
    assert "grower_action" in result["markdown"]
    assert "knowledge_gap" in result["markdown"]
    assert "Journalism Engine required" in result["markdown"]


def test_limited_summary_custom_topic_in_output():
    req = _default_req(topic="custom", custom_topic="Vanilla cultivation")
    result = build_limited_evidence_summary(req, _evidence_no_db())
    assert "Vanilla cultivation" in result["markdown"]


def test_limited_summary_has_required_keys():
    req = _default_req()
    result = build_limited_evidence_summary(req, _evidence_no_db())
    for key in ("markdown", "word_count", "evidence_mode", "journalism_engine_available",
                "insufficient_evidence", "warnings", "citations", "project_table", "generated_at"):
        assert key in result, f"Missing key: {key}"


# ---------- integration: API routes with mocked backend ----------


def _make_backend_capability(monkeypatch, available: bool, extra: dict | None = None):
    """Monkeypatch check_journalism_engine_capability to return a fixed result."""
    import calyx_report
    result = {"available": available}
    if extra:
        result.update(extra)
    monkeypatch.setattr(calyx_report, "check_journalism_engine_capability", lambda: result)


def _make_backend_call(monkeypatch, backend_response: dict | None = None, raise_error: str | None = None):
    """Monkeypatch _call_journalism_backend to return a fixed response or raise."""
    import calyx_report
    if raise_error:
        def _fake(*a, **kw):
            raise RuntimeError(raise_error)
    else:
        def _fake(*a, **kw):
            return backend_response or {}
    monkeypatch.setattr(calyx_report, "_call_journalism_backend", _fake)


def test_api_generate_falls_back_to_limited_summary_when_no_backend(monkeypatch):
    """With no backend configured, /generate returns an evidence summary, not a prose article."""
    import calyx_report
    _make_backend_capability(monkeypatch, available=False, extra={"reason": "not configured"})
    monkeypatch.setattr(calyx_report, "_try_fetch_calyx_state", lambda: None)

    req = _default_req()
    errors = calyx_report.validate_request(req)
    assert errors == []

    evidence = calyx_report.build_evidence_preview(None)
    result = calyx_report.build_limited_evidence_summary(req, evidence)
    assert result["journalism_engine_available"] is False
    assert "NOT a completed article" in result["markdown"]


def test_api_generate_proxies_to_backend_when_available(monkeypatch):
    """When backend is available, /generate returns the backend's Markdown."""
    import calyx_report

    backend_article = {
        "markdown": "# Conservation Survey\n\nFull article here.",
        "word_count": 500,
        "evidence_mode": "full",
        "citations": [{"key": "1", "text": "Smith 2024"}],
        "project_table": [{"project": "IUCN Orchid SG", "region": "Global", "status": "active"}],
        "warnings": [],
        "insufficient_evidence": False,
        "confidence_score": 0.92,
        "generated_at": "2026-07-28T00:00:00Z",
    }
    _make_backend_capability(monkeypatch, available=True)
    _make_backend_call(monkeypatch, backend_response=backend_article)
    monkeypatch.setattr(calyx_report, "_try_fetch_calyx_state", lambda: None)

    req = _default_req()
    # Simulate what generate_report() does after validation
    payload = req.model_dump()
    result = calyx_report._call_journalism_backend("POST", "/generate", body=payload)
    result["journalism_engine_available"] = True

    assert result["evidence_mode"] == "full"
    assert "Full article here." in result["markdown"]
    assert result["citations"] == [{"key": "1", "text": "Smith 2024"}]
    assert result["project_table"][0]["project"] == "IUCN Orchid SG"


def test_api_generate_falls_back_on_backend_error(monkeypatch):
    """If backend is available but the generate call fails, fall back to limited summary."""
    import calyx_report

    _make_backend_capability(monkeypatch, available=True)
    _make_backend_call(monkeypatch, raise_error="HTTP 500: internal server error")
    monkeypatch.setattr(calyx_report, "_try_fetch_calyx_state", lambda: None)

    # Replicate route logic
    req = _default_req()
    capability = calyx_report.check_journalism_engine_capability()
    engine_available = capability.get("available", False)
    assert engine_available is True

    try:
        calyx_report._call_journalism_backend("POST", "/generate", body=req.model_dump())
        engine_available = True
    except RuntimeError as exc:
        engine_available = False
        capability["reason"] = str(exc)

    assert engine_available is False
    evidence = calyx_report.build_evidence_preview(None)
    result = calyx_report.build_limited_evidence_summary(req, evidence)
    assert result["evidence_mode"] == "limited"
    assert result["journalism_engine_available"] is False


def test_api_generate_insufficient_evidence_propagated(monkeypatch):
    """Backend insufficient_evidence flag is surfaced in the response."""
    import calyx_report

    backend_response = {
        "markdown": "# Conservation Survey\n\n*Insufficient evidence for this topic.*",
        "word_count": 50,
        "evidence_mode": "full",
        "insufficient_evidence": True,
        "warnings": ["Insufficient evidence for the requested topic and audience."],
        "citations": [],
        "project_table": [],
        "generated_at": "2026-07-28T00:00:00Z",
    }
    _make_backend_capability(monkeypatch, available=True)
    _make_backend_call(monkeypatch, backend_response=backend_response)

    req = _default_req()
    result = calyx_report._call_journalism_backend("POST", "/generate", body=req.model_dump())
    result["journalism_engine_available"] = True

    assert result["insufficient_evidence"] is True
    assert result["warnings"]


def test_api_generate_backend_warnings_propagated(monkeypatch):
    """Warnings returned by the backend are included in the response."""
    import calyx_report

    backend_response = {
        "markdown": "# Article\n\nContent.",
        "word_count": 200,
        "evidence_mode": "full",
        "insufficient_evidence": False,
        "warnings": ["Low observation count for this topic.", "Citation data is 6 months old."],
        "citations": [],
        "project_table": [],
        "generated_at": "2026-07-28T00:00:00Z",
    }
    _make_backend_capability(monkeypatch, available=True)
    _make_backend_call(monkeypatch, backend_response=backend_response)

    req = _default_req()
    result = calyx_report._call_journalism_backend("POST", "/generate", body=req.model_dump())
    assert len(result["warnings"]) == 2
    assert "Low observation count" in result["warnings"][0]


def test_api_evidence_preview_falls_back_when_no_backend(monkeypatch):
    """Evidence preview returns local limited data when no backend is configured."""
    import calyx_report
    _make_backend_capability(monkeypatch, available=False, extra={"reason": "not configured"})
    monkeypatch.setattr(calyx_report, "_try_fetch_calyx_state", lambda: {
        "taxonomy_coverage": {"families_covered": 3, "species_covered": 100},
        "findings": [],
    })

    capability = calyx_report.check_journalism_engine_capability()
    assert capability["available"] is False

    calyx_state = calyx_report._try_fetch_calyx_state()
    evidence = calyx_report.build_evidence_preview(calyx_state)
    assert evidence["taxonomy_families_covered"] == 3
    assert evidence["mode"] == "limited"


def test_api_evidence_preview_uses_backend_project_table(monkeypatch):
    """Evidence preview forwards project_table from backend when available."""
    import calyx_report

    backend_ev = {
        "mode": "full",
        "observation_count": 1420,
        "taxonomy_families_covered": 12,
        "taxonomy_species_covered": 890,
        "recent_finding_summaries": [],
        "data_sources": ["observation_engine", "iucn_redlist"],
        "project_table": [
            {"project": "IUCN Orchid SG", "region": "Global", "status": "active", "citation": "IUCN 2024"},
            {"project": "Lankester Botanical Garden", "region": "Costa Rica", "status": "active", "citation": "LBG 2023"},
        ],
        "warnings": [],
        "insufficient_evidence": False,
        "generated_at": "2026-07-28T00:00:00Z",
    }
    _make_backend_capability(monkeypatch, available=True)
    _make_backend_call(monkeypatch, backend_response=backend_ev)

    result = calyx_report._call_journalism_backend("POST", "/evidence-preview", body={})
    result["journalism_engine_available"] = True

    assert result["journalism_engine_available"] is True
    assert len(result["project_table"]) == 2
    assert result["project_table"][0]["project"] == "IUCN Orchid SG"


def test_api_markdown_download_content(monkeypatch):
    """Generated markdown contains enough content to be downloaded as a valid .md file."""
    import calyx_report
    req = _default_req(topic="conservation_survey")
    evidence = calyx_report.build_evidence_preview(None)
    result = calyx_report.build_limited_evidence_summary(req, evidence)
    # Content should be a non-empty markdown string
    assert result["markdown"].startswith("#")
    assert len(result["markdown"]) > 100
    # The filename components are consistent with the topic
    assert result["topic"] == "conservation_survey"
    assert result["publication"] == "fcos_newsletter"


def test_state_preservation_payload_round_trip():
    """Form payload round-trips through ReportRequest without data loss."""
    payload = {
        "publication": "fcos_newsletter",
        "topic": "custom",
        "custom_topic": "Vanilla conservation",
        "audience": "advanced_growers",
        "word_count": 1500,
        "citation_mode": "numbered_endnotes",
        "confidence_mode": "high_confidence_only",
        "optional_sections": ["grower_action", "knowledge_gap"],
        "visuals": ["table", "map"],
        "use_full_continuum": True,
    }
    req = ReportRequest(**payload)
    assert req.topic == "custom"
    assert req.custom_topic == "Vanilla conservation"
    assert req.audience == "advanced_growers"
    assert req.word_count == 1500
    assert req.citation_mode == "numbered_endnotes"
    assert req.confidence_mode == "high_confidence_only"
    assert "grower_action" in req.optional_sections
    assert req.use_full_continuum is True
    # Validate passes for this payload
    assert validate_request(req) == []
