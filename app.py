import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import psycopg
from psycopg_pool import ConnectionPool
from fastapi import FastAPI, HTTPException, Query, Header, Request
from fastapi.responses import JSONResponse

# -----------------------------
# Logging
# -----------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("oc-control-panel")

def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "y", "on"}

def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default

def _now_ms() -> int:
    return int(time.time() * 1000)

# -----------------------------
# Config / Guards
# -----------------------------
SERVICE_NAME = os.getenv("SERVICE_NAME", "orchid-continuum-control-panel")
COMMIT = os.getenv("RENDER_GIT_COMMIT") or "unknown"

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()
REQUIRE_ADMIN_TOKEN = _truthy(os.getenv("REQUIRE_ADMIN_TOKEN", "true"))

STARTUP_GUARD_ENABLED = _truthy(os.getenv("STARTUP_GUARD_ENABLED", "true"))
ALLOW_STARTUP = _truthy(os.getenv("ALLOW_STARTUP", "true"))
EXPECTED_COMMIT = (os.getenv("EXPECTED_COMMIT") or "").strip()

DB_CONNECT_TIMEOUT_S = _get_int("DB_CONNECT_TIMEOUT_S", 5)
DB_STATEMENT_TIMEOUT_MS = _get_int("DB_STATEMENT_TIMEOUT_MS", 5000)

POOL_MIN = _get_int("DB_POOL_MIN_SIZE", 0)
POOL_MAX = _get_int("DB_POOL_MAX_SIZE", 4)
POOL_TIMEOUT = _get_int("DB_POOL_TIMEOUT_S", 5)

# Optional: for pinging harvester health endpoints (observation)
HARVESTERS_JSON = (os.getenv("HARVESTERS_JSON") or "").strip()
HARVESTER_HTTP_TIMEOUT_S = float(os.getenv("HARVESTER_HTTP_TIMEOUT_S", "2.5"))

# -----------------------------
# DB pool
# -----------------------------
pool: Optional[ConnectionPool] = None

def _build_pool() -> ConnectionPool:
    def _configure(conn: psycopg.Connection) -> None:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s;", (DB_STATEMENT_TIMEOUT_MS,))

    return ConnectionPool(
        conninfo=DATABASE_URL,
        min_size=POOL_MIN,
        max_size=POOL_MAX,
        timeout=POOL_TIMEOUT,
        configure=_configure,
        kwargs={"connect_timeout": DB_CONNECT_TIMEOUT_S},
    )

def _p() -> ConnectionPool:
    if pool is None:
        raise HTTPException(status_code=500, detail="DB pool not initialized")
    return pool

# -----------------------------
# Admin auth
# -----------------------------
def _require_admin(token_q: Optional[str], token_h: Optional[str]) -> None:
    if not REQUIRE_ADMIN_TOKEN:
        return
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not set on server")
    token = token_q or token_h
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")

# -----------------------------
# App
# -----------------------------
app = FastAPI(title="Orchid Continuum Control Panel API", version="1.0.0")

@app.on_event("startup")
def startup() -> None:
    global pool
    logger.info("DEPLOY CHECK ✅ %s commit=%s", SERVICE_NAME, COMMIT)

    if STARTUP_GUARD_ENABLED and not ALLOW_STARTUP:
        raise RuntimeError("Startup blocked by guard (ALLOW_STARTUP=false)")

    if EXPECTED_COMMIT and COMMIT != EXPECTED_COMMIT:
        raise RuntimeError("Deployment guard: commit mismatch")

    pool = _build_pool()
    # sanity ping
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")

@app.on_event("shutdown")
def shutdown() -> None:
    global pool
    if pool is not None:
        pool.close()

@app.middleware("http")
async def reqlog(request: Request, call_next):
    t0 = _now_ms()
    try:
        resp = await call_next(request)
        dt = _now_ms() - t0
        logger.info("%s %s %s %dms", request.method, request.url.path, resp.status_code, dt)
        return resp
    except Exception as e:
        dt = _now_ms() - t0
        logger.exception("ERROR %s %s after %dms: %s", request.method, request.url.path, dt, e)
        return JSONResponse(status_code=500, content={"ok": False, "detail": "Internal server error"})

# -----------------------------
# Basic endpoints
# -----------------------------
@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": SERVICE_NAME, "commit": COMMIT}

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}

@app.get("/db/ping")
def db_ping() -> Dict[str, Any]:
    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_database()::text, current_user::text, inet_server_addr()::text
                """
            )
            db, user, host = cur.fetchone()
    return {"ok": True, "db": db, "user": user, "host": host}

@app.get("/watchdog")
def watchdog(token: Optional[str] = Query(default=None), x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    t0 = _now_ms()
    db_ok = False
    err: Optional[str] = None
    try:
        with _p().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
        db_ok = True
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    return {"ok": True, "service": SERVICE_NAME, "commit": COMMIT, "db_ok": db_ok, "error": err, "ms": _now_ms() - t0}

# -----------------------------
# Harvester registry / status
# -----------------------------
@app.get("/harvesters")
def list_harvesters(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.name, r.base_url, r.sources_json, r.enabled, r.updated_at,
                       h.last_heartbeat_at, h.state, h.current_target_id, h.message
                FROM oc_harvester_registry r
                LEFT JOIN oc_harvester_heartbeat h ON h.name = r.name
                ORDER BY r.name;
                """
            )
            rows = cur.fetchall()
    harvesters = []
    for (name, base_url, sources_json, enabled, updated_at, hb_at, state, cur_target, msg) in rows:
        harvesters.append({
            "name": name,
            "base_url": base_url,
            "sources": sources_json,
            "enabled": enabled,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "heartbeat_at": hb_at.isoformat() if hb_at else None,
            "state": state,
            "current_target_id": str(cur_target) if cur_target else None,
            "message": msg,
        })
    return {"ok": True, "count": len(harvesters), "harvesters": harvesters}

@app.post("/harvesters/{name}/enable")
def enable_harvester(
    name: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oc_harvester_registry
                SET enabled = true, updated_at = now()
                WHERE name = %s
                RETURNING name, enabled;
                """,
                (name,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Harvester not found in registry")
    return {"ok": True, "name": row[0], "enabled": row[1]}

@app.post("/harvesters/{name}/disable")
def disable_harvester(
    name: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oc_harvester_registry
                SET enabled = false, updated_at = now()
                WHERE name = %s
                RETURNING name, enabled;
                """,
                (name,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Harvester not found in registry")
    return {"ok": True, "name": row[0], "enabled": row[1]}

# -----------------------------
# Commands (RUN/STOP/PAUSE/RESUME)
# -----------------------------
@app.post("/commands")
def issue_command(
    command: str = Query(..., description="RUN|STOP|PAUSE|RESUME"),
    target_harvester: Optional[str] = Query(default=None, description="Harvester name, or omit for broadcast"),
    payload: Optional[str] = Query(default=None, description="Optional JSON payload as string"),
    created_by: Optional[str] = Query(default="control-panel"),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    cmd = command.strip().upper()
    if cmd not in {"RUN", "STOP", "PAUSE", "RESUME"}:
        raise HTTPException(status_code=400, detail="Invalid command")

    payload_json = {}
    if payload:
        try:
            payload_json = json.loads(payload)
        except Exception:
            raise HTTPException(status_code=400, detail="payload must be valid JSON string")

    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oc_harvest_commands (target_harvester, command, payload_json, created_by)
                VALUES (%s, %s, %s::jsonb, %s)
                RETURNING command_id;
                """,
                (target_harvester, cmd, json.dumps(payload_json), created_by),
            )
            command_id = cur.fetchone()[0]
    return {"ok": True, "command_id": str(command_id), "command": cmd, "target_harvester": target_harvester, "payload": payload_json}

@app.get("/commands/recent")
def recent_commands(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT command_id, target_harvester, command, payload_json, created_at, created_by, status,
                       acknowledged_at, acknowledged_by, completed_at
                FROM oc_harvest_commands
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({
            "command_id": str(r[0]),
            "target_harvester": r[1],
            "command": r[2],
            "payload": r[3],
            "created_at": r[4].isoformat(),
            "created_by": r[5],
            "status": r[6],
            "ack_at": r[7].isoformat() if r[7] else None,
            "ack_by": r[8],
            "done_at": r[9].isoformat() if r[9] else None,
        })
    return {"ok": True, "count": len(out), "commands": out}

# -----------------------------
# Targets (taxonomy-selective harvest jobs)
# -----------------------------
@app.post("/targets/enqueue")
def enqueue_targets(
    rank: str = Query(..., description="genus|species|..."),
    names: str = Query(..., description="Comma-separated scientific names (or genus names if rank=genus)"),
    priority: int = Query(default=80, ge=1, le=100),
    sources: Optional[str] = Query(default=None, description="Optional JSON array string of preferred sources"),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)

    r = rank.strip().lower()
    raw = [n.strip() for n in names.split(",") if n.strip()]
    if not raw:
        raise HTTPException(status_code=400, detail="No names provided")

    sources_json = []
    if sources:
        try:
            sj = json.loads(sources)
            if isinstance(sj, list):
                sources_json = sj
        except Exception:
            raise HTTPException(status_code=400, detail="sources must be JSON array string")

    inserted = 0
    with _p().connection() as conn:
        with conn.cursor() as cur:
            for n in raw:
                genus = n.split(" ", 1)[0] if " " in n else n
                cur.execute(
                    """
                    INSERT INTO oc_harvest_targets (rank, scientific_name, genus, priority, sources_json)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    RETURNING target_id;
                    """,
                    (r, n, genus, priority, json.dumps(sources_json)),
                )
                inserted += 1
    return {"ok": True, "rank": r, "inserted": inserted, "priority": priority, "sources": sources_json}

@app.get("/targets/queue")
def targets_queue(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
    state: str = Query(default="queued"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT target_id, rank, scientific_name, genus, accepted_taxon_id, priority, sources_json, state,
                       locked_by, locked_at, started_at, finished_at, error, created_at
                FROM oc_harvest_targets
                WHERE state = %s
                ORDER BY priority DESC, created_at ASC
                LIMIT %s;
                """,
                (state, limit),
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({
            "target_id": str(r[0]),
            "rank": r[1],
            "scientific_name": r[2],
            "genus": r[3],
            "accepted_taxon_id": r[4],
            "priority": r[5],
            "sources": r[6],
            "state": r[7],
            "locked_by": r[8],
            "locked_at": r[9].isoformat() if r[9] else None,
            "started_at": r[10].isoformat() if r[10] else None,
            "finished_at": r[11].isoformat() if r[11] else None,
            "error": r[12],
            "created_at": r[13].isoformat() if r[13] else None,
        })
    return {"ok": True, "count": len(out), "targets": out}

# -----------------------------
# Coverage auditing (requires oc_taxa_universe to be populated)
# -----------------------------
@app.get("/coverage/summary")
def coverage_summary(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM oc_taxa_universe WHERE status='accepted';")
            total = cur.fetchone()[0]
            if total == 0:
                return {"ok": True, "loaded": False, "note": "oc_taxa_universe is empty (load taxonomy universe to enable coverage audits)"}
            cur.execute(
                """
                WITH cov AS (
                  SELECT accepted_taxon_id, sum(record_count) AS total_records
                  FROM oc_taxon_coverage
                  GROUP BY accepted_taxon_id
                )
                SELECT
                  sum(CASE WHEN coalesce(c.total_records,0)=0 THEN 1 ELSE 0 END) AS zero_count,
                  sum(CASE WHEN coalesce(c.total_records,0)>0 THEN 1 ELSE 0 END) AS covered_count
                FROM oc_taxa_universe u
                LEFT JOIN cov c ON c.accepted_taxon_id = u.taxon_id
                WHERE u.status='accepted';
                """
            )
            zero_count, covered_count = cur.fetchone()
    return {"ok": True, "loaded": True, "accepted_taxa": int(total), "covered": int(covered_count or 0), "zero": int(zero_count or 0)}

@app.get("/coverage/missing")
def coverage_missing(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
    rank: str = Query(default="species"),
    limit: int = Query(default=200, ge=1, le=2000),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    r = rank.strip().lower()
    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM oc_taxa_universe WHERE status='accepted';")
            total = cur.fetchone()[0]
            if total == 0:
                return {"ok": True, "loaded": False, "note": "oc_taxa_universe is empty"}
            cur.execute(
                """
                WITH cov AS (
                  SELECT accepted_taxon_id, sum(record_count) AS total_records
                  FROM oc_taxon_coverage
                  GROUP BY accepted_taxon_id
                )
                SELECT u.taxon_id, u.scientific_name, u.genus
                FROM oc_taxa_universe u
                LEFT JOIN cov c ON c.accepted_taxon_id = u.taxon_id
                WHERE u.status='accepted'
                  AND u.rank = %s
                  AND coalesce(c.total_records,0)=0
                ORDER BY u.scientific_name
                LIMIT %s;
                """,
                (r, limit),
            )
            rows = cur.fetchall()
    return {"ok": True, "loaded": True, "rank": r, "count": len(rows), "missing": [{"taxon_id": x[0], "name": x[1], "genus": x[2]} for x in rows]}

@app.get("/coverage/rare")
def coverage_rare(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
    rank: str = Query(default="species"),
    threshold: int = Query(default=5, ge=0, le=1000),
    limit: int = Query(default=200, ge=1, le=2000),
) -> Dict[str, Any]:
    _require_admin(token, x_admin_token)
    r = rank.strip().lower()
    with _p().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM oc_taxa_universe WHERE status='accepted';")
            total = cur.fetchone()[0]
            if total == 0:
                return {"ok": True, "loaded": False, "note": "oc_taxa_universe is empty"}
            cur.execute(
                """
                WITH cov AS (
                  SELECT accepted_taxon_id, sum(record_count) AS total_records
                  FROM oc_taxon_coverage
                  GROUP BY accepted_taxon_id
                )
                SELECT u.taxon_id, u.scientific_name, u.genus, coalesce(c.total_records,0) AS total_records
                FROM oc_taxa_universe u
                LEFT JOIN cov c ON c.accepted_taxon_id = u.taxon_id
                WHERE u.status='accepted'
                  AND u.rank = %s
                  AND coalesce(c.total_records,0) <= %s
                ORDER BY total_records ASC, u.scientific_name
                LIMIT %s;
                """,
                (r, threshold, limit),
            )
            rows = cur.fetchall()
    return {
        "ok": True,
        "loaded": True,
        "rank": r,
        "threshold": threshold,
        "count": len(rows),
        "rare": [{"taxon_id": x[0], "name": x[1], "genus": x[2], "total_records": int(x[3])} for x in rows],
    }
