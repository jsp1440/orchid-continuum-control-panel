# FILE: external_health.py
# Mission Control external service health checks.
#
# This module performs REAL, live outbound HTTP checks against the other
# Orchid Continuum services this program depends on - it never returns a
# hardcoded "healthy"/"ok" status. Every result here is either a real
# observed HTTP response or an honestly-reported failure to reach the
# service (timeout, DNS failure, connection refused, non-2xx/3xx status).
#
# Service list is deliberately small and evidence-backed: only services
# with a confirmed URL and health-check path in
# jsp1440/Orchid-Continuum-Brain's config/infrastructure_registry.json are
# checked. Services this program is meant to eventually observe (Research
# Station, Conservatory) but that have no confirmed URL in that registry
# are listed separately as unconfirmed/not-wired - guessing a Render or
# Cloudflare URL for them and reporting whatever that guess returns would
# itself be a form of fabrication (checking the wrong thing and presenting
# it as the real thing).
#
# Split into a pure classifier (unit-testable, no network) and an impure
# fetch (the only part that touches the network), mirroring the fetch/
# synthesize split already used throughout this codebase (calyx.py,
# observation.py, operational.py).

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends

from admin import require_admin_token

router = APIRouter(
    prefix="/api/v1/mission-control",
    tags=["Mission Control"],
    dependencies=[Depends(require_admin_token)],
)

DEFAULT_TIMEOUT_SECONDS = float(os.getenv("EXTERNAL_HEALTH_TIMEOUT_SECONDS", "5"))
USER_AGENT = "orchid-continuum-mission-control-healthcheck/1.0"

# Mirrors config/infrastructure_registry.json in jsp1440/Orchid-Continuum-Brain
# as of BUILD-InfraRegistry v1.1. Keep in sync manually until Mission Control
# can read that registry directly (it lives in a separate repository this
# service does not check out at runtime).
EXTERNAL_SERVICES: list[dict[str, str]] = [
    {
        "service_key": "calyx_backend",
        "display_name": "Calyx Backend",
        "repo": "jsp1440/orchid-calyx-backend",
        "url": "https://orchid-calyx-backend.onrender.com",
        "health_path": "/health",
    },
    {
        "service_key": "orchid_frontend",
        "display_name": "Orchid Continuum Frontend",
        "repo": "jsp1440/orchid-continuum-frontend",
        "url": "https://orchidcontinuum.org",
        "health_path": "/",
    },
    {
        "service_key": "harvester_frontend_v3",
        "display_name": "Harvester Frontend v3",
        "repo": "jsp1440/OrchidContinuumHarvester",
        "url": "https://orchidcontinuumharvester-frontend-v3.onrender.com",
        "health_path": "/",
    },
]

# Services this program should eventually observe but cannot check honestly
# yet - no confirmed URL exists in the infrastructure registry. Reported as
# a documented gap, never a guessed check.
UNCONFIRMED_SERVICES: list[dict[str, str]] = [
    {
        "service_key": "orchid_research_station",
        "display_name": "Orchid Research Station",
        "repo": "jsp1440/orchid-research-station",
        "reason": (
            "render.yaml sets healthCheckPath=/ but no deployed hostname is "
            "recorded in config/infrastructure_registry.json yet."
        ),
    },
    {
        "service_key": "orchid_conservatory",
        "display_name": "Orchid Conservatory",
        "repo": "jsp1440/orchid-conservatory",
        "reason": (
            "Deploys via Cloudflare Workers (wrangler.jsonc), not Render; not "
            "present in config/infrastructure_registry.json."
        ),
    },
]


def _fetch(url: str, timeout: float) -> tuple[int | None, str | None]:
    """The only network-touching function in this module. Never raises -
    returns (status_code, error) so callers always get an answer."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.getcode(), None
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc)
    except Exception as exc:  # DNS failure, timeout, connection refused, etc.
        return None, str(exc)


def classify(status_code: int | None, error: str | None) -> str:
    """Pure classification of a fetch result - fully unit-testable without
    a network call."""
    if status_code is None:
        return "unreachable"
    if 200 <= status_code < 400:
        return "reachable"
    return "error_status"


def check_service(
    service: dict[str, str],
    fetch: Callable[[str, float], tuple[int | None, str | None]] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    # `fetch` resolves to the module-level `_fetch` at call time (not at
    # def time) when not explicitly supplied, so tests can monkeypatch
    # `external_health._fetch` and have it take effect even for callers
    # (like get_external_service_health()) that never pass `fetch`
    # themselves.
    fetch = fetch or _fetch
    target = service["url"].rstrip("/") + service["health_path"]
    started = time.monotonic()
    status_code, error = fetch(target, timeout)
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    return {
        "service_key": service["service_key"],
        "display_name": service["display_name"],
        "repo": service["repo"],
        "checked_url": target,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "http_status": status_code,
        "latency_ms": elapsed_ms,
        "status": classify(status_code, error),
        "error": error,
    }


def summarize(checked: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in checked:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return counts


@router.get("/external-services")
def get_external_service_health():
    checked = [check_service(service) for service in EXTERNAL_SERVICES]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": (
            "Live outbound HTTP checks performed at request time against "
            "the service list in jsp1440/Orchid-Continuum-Brain's "
            "config/infrastructure_registry.json."
        ),
        "services": checked,
        "status_counts": summarize(checked),
        "unconfirmed_services": UNCONFIRMED_SERVICES,
        "unconfirmed_note": (
            "These services are not checked because no confirmed URL exists "
            "in the infrastructure registry yet. Reporting a status for them "
            "without a real check would be fabrication."
        ),
    }


# ---------- Calyx Backend telemetry proxy ----------
#
# orchid-calyx-backend already exposes its own real, live, read-only
# Mission Control telemetry API (BUILD-039/BUILD-065, app/routers/
# mission_control.py: /api/mission-control/health, /harvesters, etc.) - it
# performs its own database-backed checks (table existence via
# to_regclass(), row counts, oc_admin.ocp_execution_jobs heartbeat rows for
# harvester state). This control panel had never called it. Rather than
# reimplement those checks against a database this service does not own,
# this proxies the backend's own answer honestly: if the live call
# succeeds, its JSON is passed through with a note identifying it as a
# live proxy; if it fails, that failure is reported plainly, never
# papered over with an invented "healthy".

CALYX_BACKEND_BASE_URL = "https://orchid-calyx-backend.onrender.com"
CALYX_BACKEND_TELEMETRY_PATHS = {
    "health": "/api/mission-control/health",
    "harvesters": "/api/mission-control/harvesters",
    "runtime": "/api/mission-control/runtime",
    "research_readiness": "/api/scientific-intelligence/research-readiness",
}


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    """Real outbound GET returning parsed JSON, or an honest error payload.

    A separate network-touching function from _fetch (rather than reusing
    it) because it needs the response body, not just the status code.
    Callers that want to test without a network call monkeypatch this
    function directly (e.g. `monkeypatch.setattr(external_health,
    "_fetch_json", fake)`), since get_calyx_backend_telemetry() looks it up
    by module-level name at call time.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "http_status": exc.code, "error": str(exc), "body": None}
    except Exception as exc:
        return {"ok": False, "http_status": None, "error": str(exc), "body": None}

    try:
        parsed = json.loads(body)
    except ValueError as exc:
        return {"ok": False, "http_status": status_code, "error": f"Response was not valid JSON: {exc}", "body": None}

    return {"ok": True, "http_status": status_code, "error": None, "body": parsed}


@router.get("/calyx-backend-telemetry")
def get_calyx_backend_telemetry():
    results: dict[str, Any] = {}
    for key, path in CALYX_BACKEND_TELEMETRY_PATHS.items():
        url = CALYX_BACKEND_BASE_URL + path
        results[key] = {"checked_url": url, **_fetch_json(url, DEFAULT_TIMEOUT_SECONDS)}
    all_ok = all(item["ok"] for item in results.values())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": (
            f"Live proxy of {CALYX_BACKEND_BASE_URL}'s own read-only Mission "
            "Control telemetry endpoints (app/routers/mission_control.py in "
            "jsp1440/orchid-calyx-backend). Each field's body is that "
            "backend's own real-time answer, not recomputed here."
        ),
        "status": "reachable" if all_ok else "partially_unreachable" if any(item["ok"] for item in results.values()) else "unreachable",
        "results": results,
    }
