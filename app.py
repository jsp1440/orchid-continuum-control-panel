import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from psycopg import sql
from psycopg.errors import OperationalError
from psycopg_pool import ConnectionPool

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

# -----------------------------
# Logging
# -----------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("oc-control-panel")

# -----------------------------
# Config helpers
# -----------------------------
def getenv_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

def getenv_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return default
    try:
        return int(str(v).strip())
    except ValueError:
        return default

def now_ms() -> int:
    return int(time.time() * 1000)

# -----------------------------
# Environment / Guards
# -----------------------------
SERVICE_NAME = os.getenv("SERVICE_NAME", "orchid-continuum-control-panel")
COMMIT = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "unknown"

DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")  # may be None; that's OK for non-admin endpoints
REQUIRE_ADMIN_TOKEN = getenv_bool("REQUIRE_ADMIN_TOKEN", True)

STARTUP_GUARD_ENABLED = getenv_bool("STARTUP_GUARD_ENABLED", True)
ALLOW_STARTUP = getenv_bool("ALLOW_STARTUP", True)

DB_CONNECT_TIMEOUT_S = getenv_int("DB_CONNECT_TIMEOUT_S", 5)
DB_STATEMENT_TIMEOUT_MS = getenv_int("DB_STATEMENT_TIMEOUT_MS", 5000)

POOL_MIN_SIZE = getenv_int("DB_POOL_MIN_SIZE", 0)
POOL_MAX_SIZE = getenv_int("DB_POOL_MAX_SIZE", 4)
POOL_TIMEOUT_S = getenv_int("DB_POOL_TIMEOUT_S", 5)

HARVESTERS_JSON = os.getenv("HARVESTERS_JSON", "").strip()

# -----------------------------
# App
# -----------------------------
app = FastAPI(title="Orchid Continuum Control Panel API", version="0.2.0")

# Connection pool (initialized lazily after startup checks)
pool: Optional[ConnectionPool] = None

def _require_admin(token: Optional[str]) -> None:
    """Admin gate. If REQUIRE_ADMIN_TOKEN is true, token must match ADMIN_TOKEN."""
    if not REQUIRE_ADMIN_TOKEN:
        return

    if not ADMIN_TOKEN:
        # This is the exact error you're seeing.
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not set on server")

    if not token or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")

def _build_pool() -> ConnectionPool:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    # psycopg connection kwargs
    conninfo = DATABASE_URL

    # Build pool with conservative defaults.
    # We also set timeouts per-connection in the configure callback.
    def _configure(conn: psycopg.Connection) -> None:
        # Set per-session statement timeout (ms)
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = %s;", (DB_STATEMENT_TIMEOUT_MS,))
        except Exception as e:
            logger.warning("Failed to set statement_timeout: %s", e)

    return ConnectionPool(
        conninfo=conninfo,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        timeout=POOL_TIMEOUT_S,
        configure=_configure,
        kwargs={
            # connect_timeout is seconds (libpq)
            "connect_timeout": DB_CONNECT_TIMEOUT_S,
            # autocommit off by default; we explicitly use context managers
        },
    )

@app.on_event("startup")
def on_startup() -> None:
    global pool

    logger.info("DEPLOY CHECK ✅ service=%s commit=%s", SERVICE_NAME, COMMIT)

    # Startup Guard: prevents runaway deploy loops if you want to freeze startup
    if STARTUP_GUARD_ENABLED and not ALLOW_STARTUP:
        logger.error("STARTUP GUARD BLOCKED: ALLOW_STARTUP=false")
        # Raise to fail fast (Render will restart; you can flip ALLOW_STARTUP to true)
        raise RuntimeError("Startup blocked by STARTUP_GUARD (ALLOW_STARTUP=false)")

    # Build pool
    try:
        pool = _build_pool()
        # Quick ping at startup (fast fail if DB is unreachable)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
        logger.info("DB startup ping ✅")
    except Exception as e:
        logger.exception("DB startup ping FAILED: %s", e)
        # Fail fast so you see it immediately in logs
        raise

@app.on_event("shutdown")
def on_shutdown() -> None:
    global pool
    if pool is not None:
        try:
            pool.close()
            logger.info("DB pool closed ✅")
        except Exception as e:
            logger.warning("DB pool close warning: %s", e)

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = now_ms()
    try:
        response = await call_next(request)
        dur = now_ms() - start
        logger.info("%s %s %s %dms", request.method, request.url.path, response.status_code, dur)
        return response
    except Exception as e:
        dur = now_ms() - start
        logger.exception("ERROR %s %s after %dms: %s", request.method, request.url.path, dur, e)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

def _get_pool() -> ConnectionPool:
    if pool is None:
        raise HTTPException(status_code=500, detail="DB pool not initialized")
    return pool

# -----------------------------
# Basic endpoints
# -----------------------------
@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": SERVICE_NAME, "commit": COMMIT}

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}

@app.get("/watchdog")
def watchdog() -> Dict[str, Any]:
    """
    Render watchdog-style endpoint:
    - checks app alive
    - checks DB ping quickly
    - returns timings
    """
    t0 = now_ms()
    db_ok = False
    db_ms: Optional[int] = None
    err: Optional[str] = None

    try:
        p = _get_pool()
        t1 = now_ms()
        with p.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
        db_ok = True
        db_ms = now_ms() - t1
    except Exception as e:
        err = str(e)

    total_ms = now_ms() - t0
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "commit": COMMIT,
        "db_ok": db_ok,
        "db_ms": db_ms,
        "total_ms": total_ms,
        "error": err,
    }

@app.get("/db/ping")
def db_ping() -> Dict[str, Any]:
    try:
        p = _get_pool()
        with p.connection() as conn:
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
        return {"ok": True, "db": row[0], "user": row[1], "host": row[2]}
    except OperationalError as e:
        raise HTTPException(status_code=503, detail=f"DB connection failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------
# Admin endpoints
# -----------------------------
@app.get("/db/tables")
def db_tables(token: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    _require_admin(token)

    p = _get_pool()
    with p.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type='BASE TABLE'
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name
                """
            )
            rows = cur.fetchall()

    tables = [f"{schema}.{name}" for schema, name in rows]
    return {"ok": True, "table_count": len(tables), "tables": tables}

def _parse_harvesters_env() -> List[Dict[str, str]]:
    if not HARVESTERS_JSON:
        return []
    try:
        data = json.loads(HARVESTERS_JSON)
        if not isinstance(data, list):
            raise ValueError("HARVESTERS_JSON must be a JSON array")
        out: List[Dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if name and url:
                out.append({"name": name, "url": url})
        return out
    except Exception as e:
        logger.warning("Failed to parse HARVESTERS_JSON: %s", e)
        return []

@app.get("/harvesters/status")
def harvesters_status(token: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    """
    Returns the configured harvester endpoints from HARVESTERS_JSON.
    (Lightweight: just returns config + basic validation.)
    """
    _require_admin(token)

    harvesters = _parse_harvesters_env()
    return {
        "ok": True,
        "count": len(harvesters),
        "harvesters": harvesters,
        "note": "This endpoint returns configured harvesters from HARVESTERS_JSON. Add health-check probing in a separate worker if desired.",
    }
