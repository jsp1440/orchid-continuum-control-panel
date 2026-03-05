import json
import os
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

import psycopg

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("oc-harvester-shim")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

HARVESTER_NAME = (os.getenv("HARVESTER_NAME") or "").strip()
if not HARVESTER_NAME:
    raise RuntimeError("HARVESTER_NAME is not set (unique per harvester service)")

BASE_URL = (os.getenv("HARVESTER_BASE_URL") or "").strip()  # optional
SOURCES = (os.getenv("HARVESTER_SOURCES_JSON") or "[]").strip()  # e.g. ["gbif"]

DB_CONNECT_TIMEOUT_S = int(os.getenv("DB_CONNECT_TIMEOUT_S", "5"))
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000"))

HEARTBEAT_EVERY_S = float(os.getenv("HEARTBEAT_EVERY_S", "30"))
POLL_EVERY_S = float(os.getenv("POLL_EVERY_S", "10"))

def _conn():
    # statement_timeout set at session start
    c = psycopg.connect(
        DATABASE_URL,
        connect_timeout=DB_CONNECT_TIMEOUT_S,
        options=f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}",
    )
    return c

def ensure_registered() -> None:
    sources_json = []
    try:
        sj = json.loads(SOURCES)
        if isinstance(sj, list):
            sources_json = sj
    except Exception:
        sources_json = []

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oc_harvester_registry (name, base_url, sources_json, enabled)
                VALUES (%s, %s, %s::jsonb, true)
                ON CONFLICT (name) DO UPDATE
                SET base_url = EXCLUDED.base_url,
                    sources_json = EXCLUDED.sources_json,
                    updated_at = now();
                """,
                (HARVESTER_NAME, BASE_URL or None, json.dumps(sources_json)),
            )
            cur.execute(
                """
                INSERT INTO oc_harvester_heartbeat (name, state, message, meta_json)
                VALUES (%s, 'idle', 'registered', '{}'::jsonb)
                ON CONFLICT (name) DO NOTHING;
                """,
                (HARVESTER_NAME,),
            )

def heartbeat(state: str, message: str = "", current_target_id: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> None:
    meta = meta or {}
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oc_harvester_heartbeat
                SET last_heartbeat_at = now(),
                    state = %s,
                    current_target_id = %s::uuid,
                    message = %s,
                    meta_json = %s::jsonb
                WHERE name = %s;
                """,
                (state, current_target_id, message, json.dumps(meta), HARVESTER_NAME),
            )

def is_enabled() -> bool:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM oc_harvester_registry WHERE name=%s;", (HARVESTER_NAME,))
            row = cur.fetchone()
            if not row:
                return False
            return bool(row[0])

def fetch_next_command() -> Optional[Dict[str, Any]]:
    """
    Gets the newest queued command addressed to this harvester OR broadcast (target_harvester IS NULL).
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT command_id, target_harvester, command, payload_json
                FROM oc_harvest_commands
                WHERE status='queued'
                  AND (target_harvester = %s OR target_harvester IS NULL)
                ORDER BY created_at ASC
                LIMIT 1;
                """,
                (HARVESTER_NAME,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "command_id": str(row[0]),
                "target_harvester": row[1],
                "command": row[2],
                "payload": row[3] or {},
            }

def ack_command(command_id: str) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oc_harvest_commands
                SET status='ack',
                    acknowledged_at=now(),
                    acknowledged_by=%s
                WHERE command_id=%s::uuid;
                """,
                (HARVESTER_NAME, command_id),
            )

def complete_command(command_id: str) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oc_harvest_commands
                SET status='done',
                    completed_at=now()
                WHERE command_id=%s::uuid;
                """,
                (command_id,),
            )

def lock_next_target(preferred_sources: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """
    Atomically locks the next queued target.
    - If target.sources_json is empty -> any harvester may take it.
    - If target.sources_json has entries -> harvester should only take if overlap with its SOURCES.
    """
    my_sources: List[str] = []
    try:
        sj = json.loads(SOURCES)
        if isinstance(sj, list):
            my_sources = [str(x) for x in sj]
    except Exception:
        my_sources = []

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT target_id, rank, scientific_name, genus, accepted_taxon_id, priority, sources_json
                FROM oc_harvest_targets
                WHERE state='queued'
                ORDER BY priority DESC, created_at ASC
                LIMIT 25;
                """
            )
            candidates = cur.fetchall()

            for row in candidates:
                target_id = str(row[0])
                sources_json = row[6] or []
                allowed = True
                if isinstance(sources_json, list) and len(sources_json) > 0:
                    allowed = any(s in my_sources for s in sources_json)

                if not allowed:
                    continue

                # try to lock
                cur.execute(
                    """
                    UPDATE oc_harvest_targets
                    SET state='running',
                        locked_by=%s,
                        locked_at=now(),
                        started_at=now()
                    WHERE target_id=%s::uuid
                      AND state='queued'
                    RETURNING target_id, rank, scientific_name, genus, accepted_taxon_id, priority, sources_json;
                    """,
                    (HARVESTER_NAME, target_id),
                )
                got = cur.fetchone()
                if got:
                    return {
                        "target_id": str(got[0]),
                        "rank": got[1],
                        "scientific_name": got[2],
                        "genus": got[3],
                        "accepted_taxon_id": got[4],
                        "priority": got[5],
                        "sources": got[6] or [],
                    }

    return None

def start_run(target_id: Optional[str]) -> str:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oc_harvest_runs (target_id, harvester_name, status)
                VALUES (%s::uuid, %s, 'running')
                RETURNING run_id;
                """,
                (target_id, HARVESTER_NAME),
            )
            run_id = str(cur.fetchone()[0])
    return run_id

def finish_run(run_id: str, status: str, stats: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
    stats = stats or {}
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oc_harvest_runs
                SET finished_at=now(),
                    status=%s,
                    stats_json=%s::jsonb,
                    error=%s
                WHERE run_id=%s::uuid;
                """,
                (status, json.dumps(stats), error, run_id),
            )

def finish_target(target_id: str, ok: bool, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
    result = result or {}
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oc_harvest_targets
                SET state=%s,
                    finished_at=now(),
                    result_json=%s::jsonb,
                    error=%s
                WHERE target_id=%s::uuid;
                """,
                ("done" if ok else "failed", json.dumps(result), error, target_id),
            )

def add_coverage(accepted_taxon_id: int, source: str, records_delta: int = 0, media_delta: int = 0) -> None:
    """
    Call this from inside your harvester after inserting/updating records.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oc_taxon_coverage (accepted_taxon_id, source, record_count, media_count, last_seen_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (accepted_taxon_id, source) DO UPDATE
                SET record_count = oc_taxon_coverage.record_count + EXCLUDED.record_count,
                    media_count  = oc_taxon_coverage.media_count  + EXCLUDED.media_count,
                    last_seen_at = now();
                """,
                (accepted_taxon_id, source, records_delta, media_delta),
            )

def main_loop(run_target_fn):
    """
    run_target_fn(target: dict, stop_requested: callable) -> (ok:bool, result:dict, error:str|None)

    You implement run_target_fn in each harvester. The shim controls:
    - enable/disable
    - commands (pause/stop)
    - polling targets
    - heartbeat
    - run logging
    """
    ensure_registered()
    last_hb = 0.0
    paused = False
    stop_flag = {"stop": False}

    def stop_requested() -> bool:
        return stop_flag["stop"]

    while True:
        now = time.time()
        if now - last_hb >= HEARTBEAT_EVERY_S:
            heartbeat("paused" if paused else "idle", "waiting" if not paused else "paused")
            last_hb = now

        if not is_enabled():
            time.sleep(POLL_EVERY_S)
            continue

        cmd = fetch_next_command()
        if cmd:
            ack_command(cmd["command_id"])
            c = (cmd["command"] or "").upper()
            if c == "PAUSE":
                paused = True
            elif c == "RESUME":
                paused = False
                stop_flag["stop"] = False
            elif c == "STOP":
                stop_flag["stop"] = True
            elif c == "RUN":
                # RUN just wakes loop; target selection still controls what runs.
                paused = False
                stop_flag["stop"] = False
            complete_command(cmd["command_id"])

        if paused or stop_requested():
            time.sleep(POLL_EVERY_S)
            continue

        target = lock_next_target()
        if not target:
            time.sleep(POLL_EVERY_S)
            continue

        target_id = target["target_id"]
        heartbeat("running", f"running {target.get('scientific_name')}", current_target_id=target_id)
        run_id = start_run(target_id)

        try:
            ok, result, err = run_target_fn(target, stop_requested)
            if ok:
                finish_target(target_id, ok=True, result=result)
                finish_run(run_id, status="success", stats=result)
                heartbeat("idle", "done")
            else:
                finish_target(target_id, ok=False, result=result, error=err or "failed")
                finish_run(run_id, status="failed", stats=result, error=err)
                heartbeat("error", err or "failed", meta={"last_target": target})
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            finish_target(target_id, ok=False, result={}, error=msg)
            finish_run(run_id, status="failed", stats={}, error=msg)
            heartbeat("error", msg, meta={"last_target": target})

        time.sleep(0.5)
