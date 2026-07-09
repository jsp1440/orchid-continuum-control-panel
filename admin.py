# FILE: admin.py
# Minimal password-gate placeholder for the Orchid Continuum Admin / Control Panel.
#
# This is intentionally NOT a full authentication system: no user accounts,
# no sessions, no hashed passwords. It checks a single shared secret
# (ADMIN_PANEL_TOKEN) against an Authorization header or a `token` query
# parameter. If ADMIN_PANEL_TOKEN is not set, admin routes are disabled
# (503) rather than falling open to the public.

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["Admin"])


def _configured_admin_token() -> str:
    configured = (os.getenv("ADMIN_PANEL_TOKEN") or "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Admin panel is disabled: ADMIN_PANEL_TOKEN is not configured",
        )
    return configured


def _supplied_admin_token(
    token: Optional[str] = None,
    authorization: Optional[str] = None,
) -> str:
    supplied = (token or "").strip()
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return supplied


def require_admin_token(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> bool:
    configured = _configured_admin_token()
    supplied = _supplied_admin_token(token, authorization)

    if not supplied or supplied != configured:
        raise HTTPException(status_code=401, detail="Admin token required")

    return True


def _admin_unlock_html(message: str = "") -> str:
    message_html = f'<div class="error">{message}</div>' if message else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mission Control Access</title>
  <style>
    :root {{ --bg:#081120;--panel:#111b31;--text:#edf3ff;--muted:#b2bfdc;--border:#2a3755;--blue:#5aa2ff;--err:#ff6b81; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px;
      font-family:Arial,Helvetica,sans-serif; background:linear-gradient(180deg,#07111f 0%,#0b1630 100%); color:var(--text); }}
    main {{ width:min(480px,100%); background:var(--panel); border:1px solid var(--border); border-radius:18px; padding:24px; }}
    h1 {{ margin:0 0 8px; font-size:26px; }}
    p {{ color:var(--muted); line-height:1.5; margin:0 0 18px; }}
    label {{ display:block; font-size:13px; color:var(--muted); margin-bottom:8px; }}
    input {{ width:100%; border:1px solid var(--border); border-radius:12px; padding:12px; color:var(--text); background:#081120; }}
    button {{ margin-top:14px; width:100%; border:0; border-radius:12px; padding:12px 14px; color:#fff; background:var(--blue); font-weight:700; cursor:pointer; }}
    .error {{ border:1px solid rgba(255,107,129,.5); background:rgba(255,107,129,.12); color:#ffd9df; padding:10px 12px; border-radius:12px; margin-bottom:14px; font-size:13px; }}
    .note {{ margin-top:14px; font-size:12px; color:var(--muted); }}
  </style>
</head>
<body>
  <main>
    <h1>Mission Control Access</h1>
    <p>Enter the configured admin token to open Orchid Continuum Mission Control. Protected tools remain unavailable without the token.</p>
    {message_html}
    <form method="get" action="/admin.html">
      <label for="token">Admin token</label>
      <input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
      <button type="submit">Open Mission Control</button>
    </form>
    <div class="note">This is the existing shared-token gate. It does not enable deploy, pause, credential, or production-write controls.</div>
  </main>
</body>
</html>"""


@router.get("/admin.html")
def serve_admin_html(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
):
    configured = _configured_admin_token()
    supplied = _supplied_admin_token(token, authorization)
    if not supplied:
        return HTMLResponse(_admin_unlock_html(), status_code=200)
    if supplied != configured:
        return HTMLResponse(_admin_unlock_html("Invalid admin token."), status_code=401)

    path = Path(__file__).resolve().parent / "admin.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="admin.html not found")
    return FileResponse(path, media_type="text/html")
