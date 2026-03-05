print("DEPLOY CHECK ✅ app.py is the NEW psycopg version")
print("DEPLOY CHECK ✅ commit:", os.getenv("RENDER_GIT_COMMIT"))
import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
import psycopg


app = FastAPI(title="Orchid Continuum Control Panel API")


def get_conn() -> psycopg.Connection:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    # psycopg v3 accepts libpq-style URLs (same as psycopg2)
    return psycopg.connect(db_url)


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
                      inet_server_addr()::text AS host,
                      inet_server_port()::int AS port
                    """
                )
                row = cur.fetchone()
        return {"ok": True, "db": row[0], "user": row[1], "host": row[2], "port": row[3]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB ping failed: {e!s}")


@app.get("/harvest_state/summary")
def harvest_state_summary(source: Optional[str] = None) -> Dict[str, Any]:
    """
    Summarize harvest_state by source.
    Optional query param: ?source=orchid_central
    """
    try:
        where = ""
        params = []
        if source:
            where = "WHERE source = %s"
            params.append(source)

        sql = f"""
        SELECT source, COUNT(*) AS runs, COALESCE(SUM(total_records_harvested),0) AS records, MAX(last_run_at) AS last_run
        FROM harvest_state
        {where}
        GROUP BY source
        ORDER BY runs DESC
        """

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        return {
            "ok": True,
            "filter_source": source,
            "rows": [
                {"source": r[0], "runs": int(r[1]), "records": int(r[2]), "last_run_at": (r[3].isoformat() if r[3] else None)}
                for r in rows
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summary failed: {e!s}")
