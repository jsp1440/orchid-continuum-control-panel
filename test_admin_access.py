from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from admin import require_admin_token, serve_admin_html


def test_admin_html_without_token_shows_unlock_form_not_mission_control(monkeypatch):
    monkeypatch.setenv("ADMIN_PANEL_TOKEN", "secret-token")
    res = serve_admin_html(token=None, authorization=None)

    assert isinstance(res, HTMLResponse)
    assert res.status_code == 200
    body = res.body.decode()
    assert "Mission Control Access" in body
    assert "Admin token" in body
    assert "Engineering Memory" not in body


def test_admin_html_invalid_token_keeps_gate_closed(monkeypatch):
    monkeypatch.setenv("ADMIN_PANEL_TOKEN", "secret-token")
    res = serve_admin_html(token="wrong")

    assert isinstance(res, HTMLResponse)
    assert res.status_code == 401
    body = res.body.decode()
    assert "Invalid admin token" in body
    assert "Engineering Memory" not in body


def test_admin_html_valid_token_serves_mission_control(monkeypatch):
    monkeypatch.setenv("ADMIN_PANEL_TOKEN", "secret-token")
    res = serve_admin_html(token="secret-token")

    assert isinstance(res, FileResponse)


def test_admin_dependency_still_requires_token(monkeypatch):
    monkeypatch.setenv("ADMIN_PANEL_TOKEN", "secret-token")

    with pytest.raises(HTTPException) as exc_info:
        require_admin_token(token=None, authorization=None)
    assert exc_info.value.status_code == 401
    assert require_admin_token(token="secret-token") is True


def test_admin_html_renders_calyx_harvester_telemetry_not_just_reachability():
    """The Calyx Backend Telemetry card must render real harvester/runtime
    data (state, last run, warning counts) from the existing
    /api/v1/mission-control/calyx-backend-telemetry proxy, not merely link
    to the raw JSON -- see external_health.py's own documented next-build
    note. Source-presence check: this repo has no JS test runner, so this
    asserts the module card and its render wiring exist in the served file,
    matching the pattern of the other admin_access tests in this file."""
    body = Path("admin.html").read_text(encoding="utf-8")

    assert "Calyx Backend Telemetry" in body
    assert "calyx-backend-telemetry" in body
    assert "calyxHarvesterList" in body
    # Must render per-harvester fields, not just an overall reachability line.
    assert "h.state" in body
    assert "h.last_run" in body
    assert "h.warning_count" in body
