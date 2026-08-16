# FILE: journalism_report.py
# Mission Control Journalism Report - read-only-first proxy to the Calyx
# Backend's real journalism generation contract.
#
# WHY THIS FILE EXISTS
# ---------------------
# A prior PR (#5, "Mission Control Calyx Report") added a journalism report
# feature that targeted a retired JOURNALISM_ENGINE_URL + /capability /
# /evidence-preview / /generate contract, overlapped this repo's already-
# merged Mission Control telemetry work, and - worst of all - its "limited
# mode" build_article() contained hard-coded scientific/conservation prose
# that was not derived from the evidence object at all. That PR must never
# be merged. See jsp1440/Orchid-Continuum-Brain issue #80 for the forensic
# review.
#
# This module reconstructs only the safe, real capability: a thin,
# read-only-first proxy to the CURRENT, CANONICAL journalism contract
# exposed by orchid-calyx-backend (app/calyx_journalism/routes.py, mounted
# at /brain/journalism/... - see app/brain/routes.py in that repo). Every
# route here does exactly one thing: forward the request to that backend
# and return its real answer, honestly. This module never composes,
# templates, or otherwise synthesizes article prose itself - not even in
# a "fallback" or "limited mode" path. If the backend cannot be reached,
# or the request credential is not configured, the caller gets a
# structured, explicit "unavailable" response - never invented text.
#
# CREDENTIAL HANDLING
# --------------------
# orchid-calyx-backend gates every /brain/journalism/* route with
# verify_owner_or_api_key (app/security.py): it accepts either an
# X-API-Key header matching that backend's own CALYX_API_KEY env var, or
# an owner session cookie. This service has no owner session with that
# backend, so it authenticates the same way memory.py's Brain Outbox sync
# authenticates with BRAIN_SYNC_TOKEN: an optional control-panel-side env
# var (CALYX_BACKEND_API_KEY) is forwarded as X-API-Key only when it is
# non-empty. No credential value is ever hard-coded, guessed, or defaulted
# anywhere in this file. If CALYX_BACKEND_API_KEY is not set, every route
# below fails closed with an explicit "available: false" response instead
# of silently attempting an unauthenticated call (which the backend would
# reject anyway) or fabricating a report body.
#
# As of this file's creation, CALYX_BACKEND_API_KEY is NOT configured in
# any deployed environment, so this entire feature is a no-op in
# production until an owner explicitly supplies that credential.
#
# REQUEST/RESPONSE SHAPES
# -------------------------
# The Pydantic models below mirror app/calyx_journalism/schemas.py in
# jsp1440/orchid-calyx-backend as read directly from that repo. Kept in
# sync manually, the same way EXTERNAL_SERVICES in external_health.py is
# kept in sync with the infrastructure registry it mirrors - this service
# does not check out that repository at runtime.

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from admin import require_admin_token
from external_health import CALYX_BACKEND_BASE_URL

router = APIRouter(
    prefix="/api/v1/mission-control/journalism-report",
    tags=["Mission Control", "Journalism Report"],
    dependencies=[Depends(require_admin_token)],
)

USER_AGENT = "orchid-continuum-mission-control-journalism-report/1.0"
DEFAULT_TIMEOUT_SECONDS = float(
    os.getenv("CALYX_BACKEND_JOURNALISM_TIMEOUT_SECONDS", "20")
)

CREDENTIAL_ENV_VAR = "CALYX_BACKEND_API_KEY"
CREDENTIAL_NOT_CONFIGURED_REASON = f"{CREDENTIAL_ENV_VAR} not configured"

JOURNALISM_BASE_PATH = "/brain/journalism"


# ---------------------------------------------------------------------------
# Request schemas - mirror app/calyx_journalism/schemas.py exactly (field
# names and types), so a caller building a valid backend request builds a
# valid request here too. Backend-side validators (e.g. word-count min/max
# ordering) are intentionally not duplicated - the backend is still the
# source of truth and will reject an invalid request; duplicating that
# logic here would just be another place for it to drift out of sync.
# ---------------------------------------------------------------------------

class PublicationMeta(BaseModel):
    publication_id: str
    publication_name: str
    theme: str
    description: str | None = None
    language: str = "en"


class ArticleBrief(BaseModel):
    title: str
    focus: str
    target_word_count_min: int = 800
    target_word_count_max: int = 1500
    scope_hints: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class GenerationMode(BaseModel):
    mode: Literal["full_continuum", "limited_evidence"]
    reason: str | None = None
    unavailable_dependencies: list[str] = Field(default_factory=list)


class EvidencePreviewRequest(BaseModel):
    evidence_items: list[dict[str, Any]] = Field(default_factory=list)
    available_dependencies: list[str] = Field(default_factory=list)


class ArticleGenerationRequest(BaseModel):
    publication: PublicationMeta
    brief: ArticleBrief
    generation_mode: GenerationMode
    operator_notes: str | None = None
    evidence_packet_id: str | None = None
    evidence_items: list[dict[str, Any]] = Field(default_factory=list)


class MarkdownExportRequest(BaseModel):
    article_id: str
    publication: PublicationMeta
    brief: ArticleBrief


# ---------------------------------------------------------------------------
# Credential + outbound proxy
# ---------------------------------------------------------------------------

def _configured_backend_api_key() -> str:
    """Reads CALYX_BACKEND_API_KEY fresh on every call (never cached) so
    tests and operators can set/unset it without restarting the process."""
    return (os.getenv(CREDENTIAL_ENV_VAR) or "").strip()


def get_credential_status() -> dict[str, Any]:
    api_key = _configured_backend_api_key()
    if not api_key:
        return {"available": False, "reason": CREDENTIAL_NOT_CONFIGURED_REASON}
    return {"available": True, "reason": None}


def _require_backend_api_key() -> str:
    api_key = _configured_backend_api_key()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail={"available": False, "reason": CREDENTIAL_NOT_CONFIGURED_REASON},
        )
    return api_key


def _backend_request(
    method: str,
    path: str,
    api_key: str,
    timeout: float,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The only network-touching function in this module. Never raises -
    always returns {"ok", "http_status", "error", "body"} so callers get an
    honest answer either way. A separate function from external_health's
    _fetch_json (rather than reused) because it needs to send an auth
    header and, for POST/PUT-style calls, a JSON body.

    Tests monkeypatch this function directly (e.g.
    `monkeypatch.setattr(journalism_report, "_backend_request", fake)`),
    the same pattern external_health.py and memory.py already use, since
    every route below looks it up by module-level name at call time.
    """
    url = CALYX_BACKEND_BASE_URL + path
    headers = {"User-Agent": USER_AGENT, "X-API-Key": api_key}
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            raw_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw_body = None
        try:
            raw_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw_body = None
        parsed_error_body = None
        if raw_body:
            try:
                parsed_error_body = json.loads(raw_body)
            except ValueError:
                parsed_error_body = None
        return {
            "ok": False,
            "http_status": exc.code,
            "error": str(exc),
            "body": parsed_error_body,
        }
    except Exception as exc:  # DNS failure, timeout, connection refused, etc.
        return {"ok": False, "http_status": None, "error": str(exc), "body": None}

    if not raw_body:
        return {"ok": True, "http_status": status_code, "error": None, "body": None}
    try:
        parsed = json.loads(raw_body)
    except ValueError as exc:
        return {
            "ok": False,
            "http_status": status_code,
            "error": f"Response was not valid JSON: {exc}",
            "body": None,
        }
    return {"ok": True, "http_status": status_code, "error": None, "body": parsed}


def _proxy(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> Any:
    """Requires a configured credential, forwards the call to the real
    backend, and returns its real JSON body unchanged on success. On
    failure (backend error status, or the backend being unreachable), it
    raises an HTTPException that mirrors the backend's own status code and
    error body wherever the backend supplied one - never a locally
    synthesized success or a locally synthesized article."""
    api_key = _require_backend_api_key()
    result = _backend_request(
        method, path, api_key, DEFAULT_TIMEOUT_SECONDS, json_body
    )
    if not result["ok"]:
        status_code = result["http_status"] or 502
        detail = (
            result["body"]
            if result["body"] is not None
            else {"error": result["error"] or "Calyx Backend request failed"}
        )
        raise HTTPException(status_code=status_code, detail=detail)
    return result["body"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status")
def get_status() -> dict[str, Any]:
    """Read-only credential/availability check - never touches the
    network. Lets the UI (and operators) tell at a glance whether this
    feature can do anything at all before offering any buttons."""
    return get_credential_status()


@router.get("/presets")
def list_presets() -> Any:
    return _proxy("GET", f"{JOURNALISM_BASE_PATH}/presets")


@router.get("/presets/{preset_id}")
def get_preset(preset_id: str) -> Any:
    return _proxy("GET", f"{JOURNALISM_BASE_PATH}/presets/{preset_id}")


@router.post("/evidence-preview", status_code=201)
def evidence_preview(payload: EvidencePreviewRequest) -> Any:
    return _proxy(
        "POST",
        f"{JOURNALISM_BASE_PATH}/evidence-preview",
        json_body=payload.model_dump(),
    )


@router.get("/evidence-packets/{packet_id}")
def get_evidence_packet(packet_id: str) -> Any:
    return _proxy("GET", f"{JOURNALISM_BASE_PATH}/evidence-packets/{packet_id}")


@router.post("/generate", status_code=201)
def generate_article(payload: ArticleGenerationRequest) -> Any:
    """Passes through the backend's ArticleGenerationResponse exactly as
    received, including insufficient_evidence and unavailable_dependencies
    - these are the backend's own honest signals about evidence gaps and
    must reach the caller unmodified, never smoothed over."""
    return _proxy(
        "POST",
        f"{JOURNALISM_BASE_PATH}/generate",
        json_body=payload.model_dump(),
    )


@router.get("/articles/{article_id}")
def get_article(article_id: str) -> Any:
    return _proxy("GET", f"{JOURNALISM_BASE_PATH}/articles/{article_id}")


@router.post("/export/markdown")
def export_markdown(payload: MarkdownExportRequest) -> Any:
    return _proxy(
        "POST",
        f"{JOURNALISM_BASE_PATH}/export/markdown",
        json_body=payload.model_dump(),
    )
