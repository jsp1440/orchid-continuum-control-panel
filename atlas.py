# FILE: atlas.py
# NOT WIRED: this router is never included in app.py (see app.py's router
# list) and is not reachable in the deployed service. The real, live Atlas
# implementation is app.py's /api/orchid-widgets/region-profile,
# /api/orchid-widgets/orchids-by-region, and /api/orchid-widgets/region-
# intelligence endpoints (all backed by live database queries), consumed by
# atlas.html.
#
# This module previously returned a hardcoded "status": "active" from
# atlas_home() with no check behind it - a fabricated health status per
# this project's "never fabricate health status" rule, left over from an
# early stub. Corrected to state plainly that no check is performed here,
# since silently trusting a hardcoded value (even in unreachable code) is
# exactly the pattern that rule exists to prevent. Kept only as a documented
# stub in case a future build wires it up for real; do not add fields that
# imply a live check without one.

from fastapi import APIRouter

router = APIRouter(prefix="/atlas", tags=["Orchid Atlas"])


@router.get("/")
def atlas_home():
    return {
        "atlas": "Orchid Atlas API",
        "status": "not_wired",
        "note": (
            "This route performs no check and is not mounted in app.py. "
            "Use /api/orchid-widgets/region-profile and related endpoints "
            "in app.py for the real, live-database-backed Atlas."
        ),
    }


@router.get("/species/{name}")
def atlas_species(name: str):
    return {
        "species": name,
        "status": "not_wired",
        "message": (
            "Placeholder lookup - performs no real query. Use "
            "/api/orchid-widgets/orchids-by-region in app.py for real data."
        ),
    }
