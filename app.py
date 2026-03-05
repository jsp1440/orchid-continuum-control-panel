import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

DATABASE_URL = os.getenv("DATABASE_URL", "")

app = FastAPI(title="Orchid Continuum Control Panel")


def q(sql: str, params=None):
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
        return rows
    finally:
        conn.close()


@app.get("/api/health")
def health():
    try:
        rows = q("SELECT now() AS now, current_database() AS db;")
        return {"ok": True, **rows[0]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/api/harvesters")
def harvesters():
    rows = q("""
        SELECT source, status, last_run_at, total_records_harvested, error_text
        FROM oc_admin.v_harvester_rollup
        ORDER BY source;
    """)
    return {"count": len(rows), "items": rows}


@app.get("/", response_class=HTMLResponse)
def home():
    rows = q("""
        SELECT source, status, last_run_at, total_records_harvested, COALESCE(error_text,'') AS error_text
        FROM oc_admin.v_harvester_rollup
        ORDER BY
          CASE status WHEN 'error' THEN 0 WHEN 'ok_zero' THEN 1 ELSE 2 END,
          source;
    """)
    # Simple inline HTML to avoid template setup
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    trs = "\n".join(
        f"<tr>"
        f"<td>{esc(r['source'])}</td>"
        f"<td><b>{esc(r['status'])}</b></td>"
        f"<td>{esc(str(r['last_run_at']))}</td>"
        f"<td style='text-align:right'>{r['total_records_harvested']}</td>"
        f"<td style='max-width:520px;white-space:pre-wrap'>{esc(r['error_text'])}</td>"
        f"</tr>"
        for r in rows
    )

    html = f"""
    <html>
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>Orchid Continuum Control Panel</title>
      <style>
        body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 18px; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; font-size: 14px; }}
        th {{ background: #f6f6f6; text-align: left; }}
        .meta {{ margin-bottom: 12px; color: #444; }}
        .pill {{ display:inline-block; padding:2px 8px; border-radius:12px; background:#eee; }}
      </style>
    </head>
    <body>
      <h2>Orchid Continuum Control Panel</h2>
      <div class="meta">
        <span class="pill">DB-backed</span>
        &nbsp;|&nbsp;
        <a href="/api/health">/api/health</a>
        &nbsp;|&nbsp;
        <a href="/api/harvesters">/api/harvesters</a>
      </div>
      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th>Status</th>
            <th>Last Run</th>
            <th>Records</th>
            <th>Last Error</th>
          </tr>
        </thead>
        <tbody>
          {trs}
        </tbody>
      </table>
    </body>
    </html>
    """
    return HTMLResponse(html)
