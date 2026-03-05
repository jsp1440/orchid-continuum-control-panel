import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# ----------------------------
# Logging
# ----------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("oc-control-panel")

APP_NAME = "orchid-continuum-control-panel"
APP_COMMIT = os.getenv("RENDER_GIT_COMMIT") or os.getenv("COMMIT") or "unknown"

# ----------------------------
# Guards / Settings
# ----------------------------
REQUIRE_ADMIN_TOKEN = os.getenv("REQUIRE_ADMIN_TOKEN", "true").lower() == "true"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")  # may be None; handled by auth dependency

STARTUP_GUARD_ENABLED = os.getenv("STARTUP_GUARD_ENABLED", "true").lower() == "true"
ALLOW_STARTUP = os.getenv("ALLOW_STARTUP", "true").lower() == "true"

DB_URL = os.getenv("DATABASE_URL")

DB_CONNECT_TIMEOUT_S = int(os.getenv("DB_CONNECT_TIMEOUT_S", "5"))
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000"))

# Pool sizing (conservative defaults for small Render instances)
POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX", "3"))

# HARVESTERS_JSON: expects JSON array of objects with at least {"name","url"}
HARVESTERS_JSON_RAW = os.getenv("HARVESTERS_JSON", "[]")

# Render watchdog: we expose a heartbeat and also track last activity in memory
STARTED_AT = time.time()
LAST_REQUEST_AT = time.time()

# ----------------------------
# Helpers
# ----------------------------
def _parse_harvesters(raw: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
        if not isinstance(data, list):
            raise ValueError("HARVESTERS_JSON must be a JSON list")
        out: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if name and url:
                out.append({"name": name, "url": url})
        return out
    except Exception as e:
        log.warning("HARVESTERS_JSON parse error: %s", e)
        return []


def _require_env(name: str, value: Optional[str]) -> None:
    if not value:
        raise RuntimeError(f"{name} is not set")


def _admin_auth(request: Request) -> None:
    """Optional admin gating for non-public endpoints."""
    if not REQUIRE_ADMIN_TOKEN:
        return
    if not ADMIN_TOKEN:
        # Your screenshot showed this exact error; keep it explicit.
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not set on server")
    token = request.headers.get("x-admin-token") or request.query_params.get("admin_token")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _db_conn_kwargs() -> Dict[str, Any]:
    """
    psycopg connect kwargs.
    Use 'options' to set statement_timeout safely without parameterization.
    """
    _require_env("DATABASE_URL", DB_URL)
    # statement_timeout expects ms if provided as e.g. "-c statement_timeout=5000"
    options = f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}"
    return {
        "conninfo": DB_URL,
        "connect_timeout": DB_CONNECT_TIMEOUT_S,
        "options": options,
        "row_factory": dict_row,
    }


# Global pool
POOL: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    global POOL
    if POOL is None:
        raise RuntimeError("DB pool not initialized")
    return POOL


def db() -> psycopg.Connection:
    """
    Dependency-style helper (for non-async FastAPI usage).
    We grab a connection from pool.
    """
    pool = get_pool()
    return pool.getconn()


# ----------------------------
# App
# ----------------------------
app = FastAPI(title="Orchid Continuum Control Panel API", version="0.1.0")


@app.middleware("http")
async def _track_requests(request: Request, call_next):
    global LAST_REQUEST_AT
    LAST_REQUEST_AT = time.time()
    try:
        resp = await call_next(request)
        return resp
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
    except Exception as e:
        log.exception("Unhandled error")
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.on_event("startup")
def _startup() -> None:
    log.info("DEPLOY CHECK ✅ %s commit=%s", APP_NAME, APP_COMMIT)

    if STARTUP_GUARD_ENABLED and not ALLOW_STARTUP:
        # Prevent restart-loops from hammering DB and costing money
        log.error("STARTUP_GUARD_ENABLED=true but ALLOW_STARTUP=false; refusing to start.")
        raise RuntimeError("Startup guard: ALLOW_STARTUP is false")

    # Init pool
    global POOL
    try:
        conn_kwargs = _db_conn_kwargs()

        # ConnectionPool can take a conninfo string; pass options via kwargs:
        POOL = ConnectionPool(
            conninfo=conn_kwargs["conninfo"],
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            kwargs={
                "connect_timeout": conn_kwargs["connect_timeout"],
                "options": conn_kwargs["options"],
                "row_factory": conn_kwargs["row_factory"],
            },
            timeout=DB_CONNECT_TIMEOUT_S,
        )

        # Quick ping to validate DB works at boot (fast fail)
        with POOL.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok;")
                _ = cur.fetchone()

        log.info(
            "DB pool ready (min=%s max=%s connect_timeout_s=%s statement_timeout_ms=%s)",
            POOL_MIN_SIZE,
            POOL_MAX_SIZE,
            DB_CONNECT_TIMEOUT_S,
            DB_STATEMENT_TIMEOUT_MS,
        )
    except Exception as e:
        log.exception("Startup DB init failed: %s", e)
        # If startup guard is enabled, fail hard to avoid zombie service
        if STARTUP_GUARD_ENABLED:
            raise
        # Otherwise, allow app to come up with degraded DB endpoints
        log.warning("Continuing without DB pool (degraded mode).")
        POOL = None


@app.on_event("shutdown")
def _shutdown() -> None:
    global POOL
    if POOL is not None:
        try:
            POOL.close()
        except Exception:
            pass
        POOL = None


# ----------------------------
# Public endpoints
# ----------------------------
@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": APP_NAME,
        "commit": APP_COMMIT,
        "uptime_s": int(time.time() - STARTED_AT),
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/watchdog")
def watchdog() -> Dict[str, Any]:
    """
    Render-ish watchdog: a lightweight endpoint you can hit from an external monitor.
    """
    now = time.time()
    return {
        "ok": True,
        "service": APP_NAME,
        "commit": APP_COMMIT,
        "uptime_s": int(now - STARTED_AT),
        "last_request_ago_s": int(now - LAST_REQUEST_AT),
    }


@app.get("/db/ping")
def db_ping() -> Dict[str, Any]:
    if POOL is None:
        raise HTTPException(status_code=503, detail="DB pool unavailable")
    try:
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      current_database()::text AS db,
                      current_user::text AS user,
                      inet_server_addr()::text AS host
                    """
                )
                row = cur.fetchone()
        return {"ok": True, **(row or {})}
    except Exception as e:
        log.warning("db_ping failed: %s", e)
        raise HTTPException(status_code=503, detail="DB ping failed")


# ----------------------------
# Admin endpoints
# ----------------------------
@app.get("/db/tables")
def db_tables(request: Request) -> Dict[str, Any]:
    _admin_auth(request)
    if POOL is None:
        raise HTTPException(status_code=503, detail="DB pool unavailable")

    try:
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      schemaname::text AS schema,
                      tablename::text AS table
                    FROM pg_catalog.pg_tables
                    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY schemaname, tablename
                    """
                )
                rows = cur.fetchall() or []
        return {"ok": True, "count": len(rows), "tables": rows}
    except Exception as e:
        log.warning("db_tables failed: %s", e)
        raise HTTPException(status_code=503, detail="DB tables query failed")


@app.get("/harvester/status")
def harvester_status(request: Request) -> Dict[str, Any]:
    """
    For now: reads HARVESTERS_JSON config and reports it back.
    Later: we’ll wire this to DB heartbeat/command tables (oc_harvester_registry, etc).
    """
    _admin_auth(request)
    harvesters = _parse_harvesters(HARVESTERS_JSON_RAW)
    return {
        "ok": True,
        "count": len(harvesters),
        "harvesters": harvesters,
    }
