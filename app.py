import os
import json
import logging
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
import psycopg
from psycopg_pool import ConnectionPool

print("DEPLOY CHECK ✅ control panel starting")
print("DEPLOY CHECK commit:", os.getenv("RENDER_GIT_COMMIT"))

# --------------------------------------------------
# LOGGING
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("orchid-control-panel")

# --------------------------------------------------
# ENVIRONMENT
# --------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

STARTUP_GUARD_ENABLED = os.getenv("STARTUP_GUARD_ENABLED", "false").lower() == "true"
ALLOW_STARTUP = os.getenv("ALLOW_STARTUP", "false").lower() == "true"

DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT_S", "5"))
DB_STATEMENT_TIMEOUT = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000"))

HARVESTERS_JSON = os.getenv("HARVESTERS_JSON", "[]")

# --------------------------------------------------
# STARTUP GUARD
# --------------------------------------------------

if STARTUP_GUARD_ENABLED and not ALLOW_STARTUP:
    logger.error("🚨 STARTUP BLOCKED BY GUARD")
    raise RuntimeError("Startup guard active")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL missing")

# --------------------------------------------------
# CONNECTION POOL
# --------------------------------------------------

logger.info("Creating database pool")

pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=5,
    timeout=DB_CONNECT_TIMEOUT,
    open=True
)

# --------------------------------------------------
# FASTAPI
# --------------------------------------------------

app = FastAPI(title="Orchid Continuum Control Panel")

# --------------------------------------------------
# DATABASE CONNECTION HELPER
# --------------------------------------------------

def get_conn():

    try:
        conn = pool.getconn()

        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {DB_STATEMENT_TIMEOUT}")

        return conn

    except Exception as e:
        logger.error("Database connection failed")
        raise HTTPException(status_code=500, detail=str(e))


def release_conn(conn):
    try:
        pool.putconn(conn)
    except Exception:
        pass


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "orchid-control-panel"
    }

# --------------------------------------------------
# DATABASE PING
# --------------------------------------------------

@app.get("/db/ping")
def db_ping() -> Dict[str, Any]:

    conn = get_conn()

    try:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                current_database(),
                current_user,
                inet_server_addr()
                """
            )

            db, user, host = cur.fetchone()

        return {
            "database": db,
            "user": user,
            "host": host
        }

    finally:
        release_conn(conn)

# --------------------------------------------------
# LIST DATABASE TABLES
# --------------------------------------------------

@app.get("/db/tables")
def db_tables():

    conn = get_conn()

    try:

        with conn.cursor() as cur:

            cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema='public'
            ORDER BY table_name
            """)

            rows = cur.fetchall()

        return {
            "table_count": len(rows),
            "tables": [r[0] for r in rows]
        }

    finally:
        release_conn(conn)

# --------------------------------------------------
# RENDER WATCHDOG
# --------------------------------------------------

@app.get("/watchdog")
def watchdog():

    return {
        "status": "alive",
        "commit": os.getenv("RENDER_GIT_COMMIT"),
        "service": "orchid-control-panel"
    }

# --------------------------------------------------
# HARVESTER STATUS
# --------------------------------------------------

@app.get("/harvesters/status")
def harvester_status():

    try:
        harvesters = json.loads(HARVESTERS_JSON)
    except Exception:
        harvesters = []

    return {
        "harvester_count": len(harvesters),
        "harvesters": harvesters
    }
