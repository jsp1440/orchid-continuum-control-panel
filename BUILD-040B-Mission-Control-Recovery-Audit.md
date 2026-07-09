# BUILD-040B - Mission Control Recovery Audit

Date: 2026-07-08
Repository: jsp1440/orchid-continuum-control-panel
Status: Fix implemented, pending deployment

## Objective

Mission Control was still inaccessible while Atlas was working correctly. The goal was to verify all admin routes, identify the difference between Atlas and Mission Control, compare against the last known working implementation, and apply the smallest safe fix without weakening security.

## Deployed Route Verification

Checked against:

```text
https://orchid-continuum-control-panel.onrender.com
```

| Route | Deployed result without token | Interpretation |
| --- | ---: | --- |
| /health | 200 | Service is healthy. |
| /db/ping | 200 | Database connection is healthy. |
| /atlas.html | 200 | Atlas is public and operational. |
| /admin.html | 401 | Admin token gate is active. |
| /engineering-memory.html | 401 | Admin token gate is active. |
| /agents.html | 401 | Admin token gate is active. |
| /calyx.html | 401 | Admin token gate is active. |
| /observations.html | 401 | Admin token gate is active. |

I did not have the production ADMIN_PANEL_TOKEN value, so I could not verify a successful authenticated request against Render. Local tests verify the authenticated code path.

## Which Routes Require ADMIN_PANEL_TOKEN

| Route | Requires ADMIN_PANEL_TOKEN? | Notes |
| --- | --- | --- |
| /admin.html | Yes for Mission Control content | Now shows an unlock form without a token; serves Mission Control only with a valid token. |
| /engineering-memory.html | Yes | Remains protected by require_admin_token. |
| /agents.html | Yes | Remains protected by require_admin_token. |
| /calyx.html | Yes | Remains protected by require_admin_token. |
| /observations.html | Yes | Remains protected by require_admin_token. |
| /atlas.html | No | Public Atlas route; intentionally not admin-gated. |

## Root Cause

Atlas is operational because `/atlas.html` is intentionally public and does not depend on `ADMIN_PANEL_TOKEN`. Mission Control is inaccessible in normal browser use because `/admin.html` itself was protected by the shared-token dependency and returned a raw 401 JSON response when opened without `?token=...`. After `ADMIN_PANEL_TOKEN` was configured, the service correctly stopped returning 503, but it still had no browser-facing unlock path. A user who navigates to `/admin.html` sees an authorization failure rather than a way to enter the configured token.

This is an access UX failure, not an Atlas, database, image, region-query, or service-health failure.

## Comparison to Last Known Working Version

The current `main` branch contains the Mission Control HTML pages and token-forwarding scripts, but it does not include a server-rendered token entry path. Earlier Mission Control iterations assumed direct navigation to:

```text
/admin.html?token=<ADMIN_PANEL_TOKEN>
```

That works only if the operator already knows to manually construct the URL. It is brittle after a Render env-var recovery because the natural check, `/admin.html`, still looks broken.

## Fix Implemented

Changed only `admin.py`:

- Kept `require_admin_token` for protected admin pages and APIs.
- Added shared helpers for configured/supplied token resolution.
- Changed `/admin.html` from dependency-gated JSON failure to a route that:
  - returns a small unlock form when no token is supplied;
  - returns the same unlock form with 401 when the token is invalid;
  - serves the existing `admin.html` Mission Control page only when the token is valid.

Added `test_admin_access.py`:

- no token shows unlock form, not Mission Control module content;
- invalid token returns 401 and does not expose Mission Control module content;
- valid token serves the existing Mission Control page;
- the admin dependency still rejects missing tokens and accepts the configured token.

## Security Boundary

This fix does not:

- remove authentication from admin pages;
- make Engineering Memory, Agents, Calyx, Observations, or admin APIs public;
- add deploy/run/pause/credential/production-write controls;
- change Atlas behavior;
- rename routes.

It only makes the existing shared-token gate usable from a browser.

## Verification

Commands run locally:

```text
python -m pytest test_admin_access.py
python -m pytest test_calyx.py test_evaluation.py test_observation.py
python -c "import app; print(len(app.app.routes)); ..."
```

Results:

```text
test_admin_access.py: 4 passed
test_calyx.py/test_evaluation.py/test_observation.py: 47 passed
app import succeeded; 59 routes registered
Relevant routes registered: /admin.html, /atlas.html, /engineering-memory.html, /agents.html, /calyx.html, /observations.html
```

## Deployment Note

Frontend/static-app redesign is not required. Backend service redeploy is required after this server-side route fix is merged so `/admin.html` can render the unlock form.
