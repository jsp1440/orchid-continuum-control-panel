import os
import json
import time
import logging
from typing import Any, Dict, List, Optional, Tuple
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

import psycopg

# =========================
# Logging (structured-ish)
# =========================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("oc-control-panel")

COMMIT = os.getenv("RENDER_GIT_COMMIT", "")
SERVICE_NAME = os.getenv("SERVICE_NAME", "orchid-continuum-control-panel")

logger.info("DEPLOY CHECK ✅ starting %s commit=%s", SERVICE_NAME, COMMIT)


# =========================
# Guards / Safety Controls
# =========================

def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "y", "on"}

STARTUP_GUARD_ENABLED = _truthy(os.getenv("STARTUP_GUARD_ENABLED", "true"))
ALLOW_STARTUP = _truthy(os.getenv("ALLOW_STARTUP", "true"))  # flip to false to hard-stop
EXPECTED_COMMIT = (os.getenv("EXPECTED_COMMIT") or "").strip()

# Admin token protects “operational” endpoints (tables, harvester status, watchdog)
ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()
REQUIRE_ADMIN_TOKEN = _truthy(os.getenv("REQUIRE_ADMIN_TOKEN", "true"))

# DB timeout protections
DB_CONNECT_TIMEOUT_S = int(os.getenv("DB_CONNECT_TIMEOUT_S", "5"))       # connect timeout
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000"))  # query timeout

# Connection pooling knobs (simple, safe, no extra libs)
POOL_MAX = int(os.getenv("DB_POOL_MAX", "5"))
POOL_IDLE_TTL_S = int(os.getenv("DB_POOL_IDLE_TTL_S", "300"))  # close idle conns after this


# =========================
# Simple connection pool
# =========================

class SimpleConnPool:
    """
    A minimal, safe pool that reuses a few psycopg connections.
    - Not as feature-rich as psycopg_pool, but avoids new deps.
    - Enforces statement_timeout and connect_timeout.
    - Closes idle conns periodically.
    """
    def __init__(self, dsn: str, max_size: int) -> None:
        self.dsn = dsn
        self.max_size = max_size
        self._pool: List[Tuple[psycopg.Connection, float]] = []  # (conn, last_used_epoch)

    def _new_conn(self) -> psycopg.Connection:
        # options sets server-side statement_timeout
        options = f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}"
        conn = psycopg.connect(
            self.dsn,
            connect_timeout=DB_CONNECT_TIMEOUT_S,
            options=options,
        )
        return conn

    def _reap_idle(self) -> None:
        if not self._pool:
            return
        now = time.time()
        keep: List[Tuple[psycopg.Connection, float]] = []
        for conn, last_used in self._pool:
            if (now - last_used) > POOL_IDLE_TTL_S:
                try:
                    conn.close()
                except Exception:
                    pass
            else:
                keep.append((conn, last_used))
        self._pool = keep

    @contextmanager
    def get(self):
        self._reap_idle()
        conn: Optional[psycopg.Connection] = None

        # Take a conn if available
        if self._pool:
            conn, _ = self._pool.pop()

        # Or create a new one if under max
        if conn is None:
            if len(self._pool) < self.max_size:
                conn = self._new_conn()
            else:
                # Should be rare given we pop above; defensive
                conn = self._new_conn()

        try:
            yield conn
        finally:
            try:
                # Ensure we're not returning a dead connection
                if conn.closed:
                    return
                self._pool.append((conn, time.time()))
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass

    def close_all(self) -> None:
        for conn, _ in self._pool:
            try:
                conn.close()
            except Exception:
                pass
        self._pool = []


def _require_admin(request: Request) -> None:
    if not REQUIRE_ADMIN_TOKEN:
        return
    if not ADMIN_TOKEN:
        # If you require a token, you must set one
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not set on server")
    got = request.headers.get("x-admin-token") or request.query_params.get("token")
    if got != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# =========================
# DB wiring
# =========================

def _get_db_url() -> str:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    return db_url

DB_URL = _get_db_url()
POOL = SimpleConnPool(DB_URL, max_size=POOL_MAX)


# =========================
# App
# =========================

app = FastAPI(
    title="Orchid Continuum Control Panel API",
    version="0.1.0",
)

# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    try:
        response = await call_next(request)
        return response
    finally:
        ms = int((time.time() - start) * 1000)
        logger.info(
            "request method=%s path=%s status=%s ms=%s ip=%s",
            request.method,
            request.url.path,
            getattr(locals().get("response", None), "status_code", "NA"),
            ms,
            request.client.host if request.client else "NA",
        )

# Guard checks on startup
@app.on_event("startup")
def startup_checks() -> None:
    if STARTUP_GUARD_ENABLED and not ALLOW_STARTUP:
        logger.error("Startup guard blocked launch: ALLOW_STARTUP is false")
        raise RuntimeError("Startup blocked by guard (ALLOW_STARTUP=false)")

    if EXPECTED_COMMIT:
        if not COMMIT:
            logger.error("Deployment guard set but RENDER_GIT_COMMIT missing")
            raise RuntimeError("Deployment guard: RENDER_GIT_COMMIT missing")
        if COMMIT != EXPECTED_COMMIT:
            logger.error("Deployment guard mismatch: expected=%s got=%s", EXPECTED_COMMIT, COMMIT)
            raise RuntimeError("Deployment guard: commit mismatch")

    # Optional: sanity DB ping at startup (fast, with timeouts)
    try:
        with POOL.get() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        logger.info("startup db_sanity=ok")
    except Exception as e:
        logger.error("startup db_sanity=fail err=%s:%s", type(e).__name__, e)
        # Don’t hard-fail by default; you can enforce with REQUIRE_DB_ON_STARTUP=true
        if _truthy(os.getenv("REQUIRE_DB_ON_STARTUP", "false")):
            raise


@app.on_event("shutdown")
def shutdown_cleanup() -> None:
    try:
        POOL.close_all()
    except Exception:
        pass


# =========================
# Routes
# =========================

@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "commit": COMMIT,
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/db/ping")
def db_ping() -> Dict[str, Any]:
    try:
        with POOL.get() as conn:
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
                if not row:
                    raise RuntimeError("DB ping returned no rows")
                return {"ok": True, "db": row[0], "user": row[1], "host": row[2]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB ping failed: {type(e).__name__}: {e}")


@app.get("/db/tables")
def db_tables(request: Request) -> Dict[str, Any]:
    _require_admin(request)

    # Optional schema filter
    schema = request.query_params.get("schema", "public")
    try:
        with POOL.get() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_type='BASE TABLE'
                      AND table_schema = %s
                    ORDER BY table_name;
                    """,
                    (schema,),
                )
                rows = cur.fetchall()
        return {
            "ok": True,
            "schema": schema,
            "count": len(rows),
            "tables": [{"schema": r[0], "table": r[1]} for r in rows],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB tables failed: {type(e).__name__}: {e}")


@app.get("/harvesters/status")
def harvesters_status(request: Request) -> Dict[str, Any]:
    """
    Configure harvesters via env var HARVESTERS_JSON like:
    [
      {"name":"gbif-worker","url":"https://your-worker.onrender.com/health"},
      {"name":"idigbio-worker","url":"https://.../health"}
    ]
    """
    _require_admin(request)

    harvesters_json = (os.getenv("HARVESTERS_JSON") or "").strip()
    if not harvesters_json:
        return {
            "ok": True,
            "configured": False,
            "harvesters": [],
            "note": "Set HARVESTERS_JSON env var to enable checks.",
        }

    try:
        harvesters = json.loads(harvesters_json)
        if not isinstance(harvesters, list):
            raise ValueError("HARVESTERS_JSON must be a JSON list")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HARVESTERS_JSON invalid: {type(e).__name__}: {e}")

    # We use httpx if available; otherwise fall back to urllib.
    timeout_s = float(os.getenv("HARVESTER_HTTP_TIMEOUT_S", "3"))
    results: List[Dict[str, Any]] = []

    try:
        import httpx  # type: ignore
        with httpx.Client(timeout=timeout_s) as client:
            for h in harvesters:
                name = str(h.get("name", "unknown"))
                url = str(h.get("url", "")).strip()
                if not url:
                    results.append({"name": name, "ok": False, "error": "missing url"})
                    continue
                try:
                    r = client.get(url, headers={"accept": "application/json"})
                    results.append({"name": name, "ok": r.status_code < 400, "status": r.status_code, "url": url})
                except Exception as e:
                    results.append({"name": name, "ok": False, "error": f"{type(e).__name__}: {e}", "url": url})
    except Exception:
        # Fallback without httpx
        import urllib.request
        for h in harvesters:
            name = str(h.get("name", "unknown"))
            url = str(h.get("url", "")).strip()
            if not url:
                results.append({"name": name, "ok": False, "error": "missing url"})
                continue
            try:
                req = urllib.request.Request(url, headers={"accept": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    results.append({"name": name, "ok": resp.status < 400, "status": resp.status, "url": url})
            except Exception as e:
                results.append({"name": name, "ok": False, "error": f"{type(e).__name__}: {e}", "url": url})

    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "configured": True, "count": len(results), "ok_count": ok_count, "harvesters": results}


@app.get("/watchdog")
def watchdog(request: Request) -> Dict[str, Any]:
    """
    Render watchdog: a single endpoint that checks:
    - app ok
    - db ok (fast)
    - commit info
    """
    _require_admin(request)

    start = time.time()
    db_ok = False
    db_err: Optional[str] = None

    try:
        with POOL.get() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        db_ok = True
    except Exception as e:
        db_err = f"{type(e).__name__}: {e}"

    ms = int((time.time() - start) * 1000)
    payload = {
        "ok": True,
        "service": SERVICE_NAME,
        "commit": COMMIT,
        "db_ok": db_ok,
        "db_error": db_err,
        "elapsed_ms": ms,
        "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return payload


# Nice JSON error for unexpected exceptions
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled path=%s err=%s:%s", request.url.path, type(exc).__name__, exc)
    return JSONResponse(status_code=500, content={"ok": False, "error": f"{type(exc).__name__}: {exc}"})
