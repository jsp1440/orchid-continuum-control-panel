import os
from typing import Any, Dict

print("DEPLOY CHECK ✅ app.py is the NEW psycopg version")
print("DEPLOY CHECK ✅ commit:", os.getenv("RENDER_GIT_COMMIT"))

from fastapi import FastAPI, HTTPException
import psycopg

app = FastAPI(title="Orchid Continuum Control Panel API")


def get_conn() -> psycopg.Connection:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    # psycopg v3 accepts libpq-style URLs (same as psycopg2)
    return psycopg.connect(db_url)


@app.get("/")
def root() -> Dict[str, Any]:
    # Render (and some monitors) hit "/" with HEAD/GET; avoid noisy 404s.
    return {
        "ok": True,
        "service": "orchid-continuum-control-panel",
        "commit": os.getenv("RENDER_GIT_COMMIT"),
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    # Basic app health
    return {"ok": True}


@app.get("/db/ping")
def db_ping() -> Dict[str, Any]:
    # Verifies DB connectivity and returns identity info
    try:
        with get_conn() as conn:
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

                return {
                    "ok": True,
                    "db": row[0],
                    "user": row[1],
                    "host": row[2],
                }
    except Exception as e:
        # Keep this message simple but useful for debugging
        raise HTTPException(status_code=500, detail=f"DB ping failed: {type(e).__name__}: {e}")
