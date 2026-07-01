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
from fastapi.responses import FileResponse

router = APIRouter(tags=["Admin"])


def require_admin_token(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> bool:
    configured = (os.getenv("ADMIN_PANEL_TOKEN") or "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Admin panel is disabled: ADMIN_PANEL_TOKEN is not configured",
        )

    supplied = (token or "").strip()
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()

    if not supplied or supplied != configured:
        raise HTTPException(status_code=401, detail="Admin token required")

    return True


@router.get("/admin.html", dependencies=[Depends(require_admin_token)])
def serve_admin_html():
    path = Path(__file__).resolve().parent / "admin.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="admin.html not found")
    return FileResponse(path, media_type="text/html")
