import pytest
from fastapi import HTTPException

import journalism_report
from journalism_report import (
    ArticleBrief,
    ArticleGenerationRequest,
    EvidencePreviewRequest,
    GenerationMode,
    MarkdownExportRequest,
    PublicationMeta,
    get_credential_status,
    get_status,
)


# ---------- credential-not-configured fail-closed behavior ----------

def test_get_credential_status_reports_unavailable_when_unset(monkeypatch):
    monkeypatch.delenv("CALYX_BACKEND_API_KEY", raising=False)
    result = get_credential_status()
    assert result == {
        "available": False,
        "reason": "CALYX_BACKEND_API_KEY not configured",
    }


def test_get_credential_status_reports_unavailable_when_blank(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "   ")
    result = get_credential_status()
    assert result["available"] is False


def test_get_credential_status_reports_available_when_set(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "some-configured-value")
    result = get_credential_status()
    assert result == {"available": True, "reason": None}


def test_get_status_route_matches_get_credential_status(monkeypatch):
    monkeypatch.delenv("CALYX_BACKEND_API_KEY", raising=False)
    assert get_status() == get_credential_status()


def test_list_presets_fails_closed_without_credential_and_never_calls_network(monkeypatch):
    monkeypatch.delenv("CALYX_BACKEND_API_KEY", raising=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("network must not be touched when no credential is configured")

    monkeypatch.setattr(journalism_report, "_backend_request", fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        journalism_report.list_presets()
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "available": False,
        "reason": "CALYX_BACKEND_API_KEY not configured",
    }


def test_generate_article_fails_closed_without_credential(monkeypatch):
    monkeypatch.delenv("CALYX_BACKEND_API_KEY", raising=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("network must not be touched when no credential is configured")

    monkeypatch.setattr(journalism_report, "_backend_request", fail_if_called)

    payload = ArticleGenerationRequest(
        publication=PublicationMeta(
            publication_id="pub-1", publication_name="Test Pub", theme="conservation"
        ),
        brief=ArticleBrief(title="Title", focus="Focus statement"),
        generation_mode=GenerationMode(mode="limited_evidence"),
    )
    with pytest.raises(HTTPException) as exc_info:
        journalism_report.generate_article(payload)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["available"] is False


# ---------- successful passthrough of each route ----------

def _fake_ok(body):
    def fake(method, path, api_key, timeout, json_body=None):
        assert api_key == "configured-test-key"
        return {"ok": True, "http_status": 200, "error": None, "body": body}
    return fake


def test_list_presets_passes_through_backend_body(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")
    body = {"count": 2, "presets": [{"preset_id": "p1"}, {"preset_id": "p2"}]}
    monkeypatch.setattr(journalism_report, "_backend_request", _fake_ok(body))
    assert journalism_report.list_presets() == body


def test_get_preset_passes_through_and_uses_correct_path(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")
    seen = {}

    def fake(method, path, api_key, timeout, json_body=None):
        seen["method"] = method
        seen["path"] = path
        return {"ok": True, "http_status": 200, "error": None, "body": {"preset_id": "p1"}}

    monkeypatch.setattr(journalism_report, "_backend_request", fake)
    result = journalism_report.get_preset("p1")
    assert result == {"preset_id": "p1"}
    assert seen["method"] == "GET"
    assert seen["path"] == "/brain/journalism/presets/p1"


def test_evidence_preview_forwards_body_and_returns_packet(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")
    packet = {
        "packet_id": "pk-1",
        "items": [],
        "item_count": 0,
        "verified_projects": [],
        "unavailable_dependencies": ["scientific_literature"],
        "mode": "limited_evidence",
    }
    seen = {}

    def fake(method, path, api_key, timeout, json_body=None):
        seen["json_body"] = json_body
        return {"ok": True, "http_status": 201, "error": None, "body": packet}

    monkeypatch.setattr(journalism_report, "_backend_request", fake)
    payload = EvidencePreviewRequest(evidence_items=[{"source_id": "x"}])
    result = journalism_report.evidence_preview(payload)
    assert result == packet
    assert seen["json_body"] == {
        "evidence_items": [{"source_id": "x"}],
        "available_dependencies": [],
    }


def test_generate_article_passes_through_insufficient_evidence_honestly(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")
    article = {
        "article_id": "art-1",
        "title": "Title",
        "mode": "limited_evidence",
        "word_count": 40,
        "sections": [{"heading": "Evidence Summary", "body": "", "citations": []}],
        "verified_projects": [],
        "unavailable_dependencies": ["scientific_literature", "pollinator_network"],
        "warnings": ["Evidence was insufficient to meet requested word count."],
        "insufficient_evidence": True,
    }
    monkeypatch.setattr(journalism_report, "_backend_request", _fake_ok(article))

    payload = ArticleGenerationRequest(
        publication=PublicationMeta(
            publication_id="pub-1", publication_name="Test Pub", theme="conservation"
        ),
        brief=ArticleBrief(title="Title", focus="Focus statement"),
        generation_mode=GenerationMode(
            mode="limited_evidence", unavailable_dependencies=["scientific_literature"]
        ),
    )
    result = journalism_report.generate_article(payload)
    assert result == article
    assert result["insufficient_evidence"] is True
    assert result["unavailable_dependencies"] == ["scientific_literature", "pollinator_network"]


def test_export_markdown_passes_through(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")
    export = {"article_id": "art-1", "filename": "art-1.md", "content": "# Title", "word_count": 40}
    monkeypatch.setattr(journalism_report, "_backend_request", _fake_ok(export))

    payload = MarkdownExportRequest(
        article_id="art-1",
        publication=PublicationMeta(
            publication_id="pub-1", publication_name="Test Pub", theme="conservation"
        ),
        brief=ArticleBrief(title="Title", focus="Focus statement"),
    )
    assert journalism_report.export_markdown(payload) == export


# ---------- honest error passthrough on backend failure/404 ----------

def test_get_preset_404_passes_through_backend_error_code(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")

    def fake(method, path, api_key, timeout, json_body=None):
        return {
            "ok": False,
            "http_status": 404,
            "error": "HTTP Error 404: Not Found",
            "body": {"code": "PRESET_NOT_FOUND", "preset_id": "missing"},
        }

    monkeypatch.setattr(journalism_report, "_backend_request", fake)
    with pytest.raises(HTTPException) as exc_info:
        journalism_report.get_preset("missing")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == {"code": "PRESET_NOT_FOUND", "preset_id": "missing"}


def test_get_article_404_passes_through_backend_error_code(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")

    def fake(method, path, api_key, timeout, json_body=None):
        return {
            "ok": False,
            "http_status": 404,
            "error": "HTTP Error 404: Not Found",
            "body": {"code": "ARTICLE_NOT_FOUND", "article_id": "missing"},
        }

    monkeypatch.setattr(journalism_report, "_backend_request", fake)
    with pytest.raises(HTTPException) as exc_info:
        journalism_report.get_article("missing")
    assert exc_info.value.status_code == 404


def test_generate_article_reports_unreachable_backend_honestly_not_as_success(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")

    def fake(method, path, api_key, timeout, json_body=None):
        return {"ok": False, "http_status": None, "error": "Connection refused", "body": None}

    monkeypatch.setattr(journalism_report, "_backend_request", fake)

    payload = ArticleGenerationRequest(
        publication=PublicationMeta(
            publication_id="pub-1", publication_name="Test Pub", theme="conservation"
        ),
        brief=ArticleBrief(title="Title", focus="Focus statement"),
        generation_mode=GenerationMode(mode="limited_evidence"),
    )
    with pytest.raises(HTTPException) as exc_info:
        journalism_report.generate_article(payload)
    # No status code was ever returned by the backend (unreachable) - this
    # must surface as a gateway failure, never a fabricated 200/success.
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == {"error": "Connection refused"}


def test_evidence_preview_non_json_backend_response_is_reported_as_error(monkeypatch):
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")

    def fake(method, path, api_key, timeout, json_body=None):
        return {
            "ok": False,
            "http_status": 200,
            "error": "Response was not valid JSON: Expecting value",
            "body": None,
        }

    monkeypatch.setattr(journalism_report, "_backend_request", fake)
    with pytest.raises(HTTPException) as exc_info:
        journalism_report.evidence_preview(EvidencePreviewRequest())
    assert exc_info.value.status_code == 200
    assert "not valid JSON" in exc_info.value.detail["error"]


# ---------- no code path ever synthesizes article prose locally ----------

def test_proxy_never_returns_body_when_backend_call_fails(monkeypatch):
    """_proxy must raise, not return a locally-built stand-in value, on any
    kind of backend failure - the only way a caller gets article content is
    the real backend's own response body."""
    monkeypatch.setenv("CALYX_BACKEND_API_KEY", "configured-test-key")

    def fake(method, path, api_key, timeout, json_body=None):
        return {"ok": False, "http_status": 500, "error": "boom", "body": None}

    monkeypatch.setattr(journalism_report, "_backend_request", fake)
    with pytest.raises(HTTPException):
        journalism_report._proxy("GET", "/brain/journalism/presets")


def test_module_source_contains_no_hardcoded_article_prose_markers():
    """Guards against reintroducing stale PR #5's build_article() pattern:
    this module must never define its own article-building function, call
    the retired JOURNALISM_ENGINE_URL contract, or return a string literal
    from any of its route handlers - every successful route return value
    must be a variable sourced from the real backend response, not a
    locally-authored constant standing in for one."""
    import ast
    import inspect

    source = inspect.getsource(journalism_report)
    # Strip comment/docstring lines before scanning for banned markers, since
    # this module's own header comment *names* stale PR #5's build_article()
    # and JOURNALISM_ENGINE_URL precisely to explain why they must never
    # reappear in actual code - only executable lines matter here.
    code_lines = [
        line for line in source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    tree = ast.parse(source)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstring_lines.update(doc.splitlines())
    code_only = "\n".join(
        line for line in code_lines if line.strip() not in docstring_lines
    )
    assert "build_article" not in code_only
    assert "JOURNALISM_ENGINE_URL" not in code_only
    assert "/capability" not in code_only
    assert "/evidence-preview" in source  # real contract path is expected

    route_function_names = {
        "get_status",
        "list_presets",
        "get_preset",
        "evidence_preview",
        "get_evidence_packet",
        "generate_article",
        "get_article",
        "export_markdown",
    }
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in route_function_names:
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
                    if isinstance(stmt.value.value, str):
                        raise AssertionError(
                            f"{node.name} returns a hardcoded string literal "
                            f"instead of a backend-sourced value: {stmt.value.value!r}"
                        )


def test_credential_status_never_includes_fabricated_report_fields():
    status = get_credential_status()
    assert set(status.keys()) == {"available", "reason"}
    assert "sections" not in status
    assert "article" not in status
