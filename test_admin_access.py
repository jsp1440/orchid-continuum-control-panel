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
