# Structural regression tests for the mobile/iPad-first Calyx conversation
# shell (calyx.html) — OWNER-CALYX-MOBILE-001, control-panel issue #11.
#
# calyx.html is a self-contained client with no build step and no JS test
# runner in this repo, so these tests lock the *first executable slice*
# invariants by reading the file directly (the same read-and-assert pattern
# the frontend repo uses for source-integrity guards). They intentionally do
# not execute JS; they guard the contract the shell must keep so it cannot
# silently regress into a desktop dashboard, grow a second Calyx, ship browser
# model keys, or fabricate integration.

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def _html():
    with open(os.path.join(HERE, "calyx.html"), encoding="utf-8") as fh:
        return fh.read()


def test_mobile_ipad_first_responsive():
    html = _html()
    assert '<meta name="viewport"' in html
    assert "width=device-width" in html
    # cover the notch / safe areas and use dynamic viewport height for phones
    assert "viewport-fit=cover" in html
    assert "100dvh" in html
    assert "env(safe-area-inset" in html
    # explicit responsive breakpoints for phone vs tablet
    assert "@media (max-width:820px)" in html
    assert "@media (min-width:821px)" in html
    # readable, tappable: a 44px tap target token that the controls reference
    assert "--tap:44px" in html


def test_persistent_multi_turn_session_identity():
    html = _html()
    assert "oc.calyx.session.v1" in html
    assert "localStorage" in html
    # multi-turn transcript, not single-shot: turns are appended and persisted
    assert "session.turns.push" in html
    assert "saveSession" in html
    assert "resetSession" in html
    # a stable session id is generated and reused
    assert "sessionId" in html
    assert "randomUUID" in html


def test_reuses_canonical_server_side_calyx_path_only():
    html = _html()
    # reuse the EXISTING canonical Calyx endpoints — do not fork a new Calyx API
    assert "/api/v1/calyx/ask" in html
    assert "/api/v1/calyx/mission-brief" in html
    # convergence rule: no second Calyx / second API surface
    assert "/api/v2/" not in html
    # each turn is grounded by a real server call (POST to the ask endpoint)
    assert re.search(r'api\(\s*API\.ask', html)


def test_no_browser_model_keys():
    html = _html().lower()
    # server-side synthesis only — the browser must never carry a model key
    for forbidden in ("openai", "anthropic", "api_key", "apikey", "sk-", "bearer sk"):
        assert forbidden not in html


def test_bounded_citation_deep_links():
    html = _html()
    # citations are parsed and turned into deep-links into the owning module
    assert "MODULE_FOR_TYPE" in html
    assert "moduleHref" in html
    assert "focus=" in html
    # bounded context only: the deep-link carries type:id focus, not evidence
    assert "encodeURIComponent(type + \":\" + id)" in html
    # modules that exist in this repo are the link targets
    for page in ("engineering-memory.html", "admin.html", "agents.html"):
        assert page in html


def test_truthful_degraded_states_no_fabrication():
    html = _html()
    up = html.upper()
    for state in ("UNAVAILABLE", "DEGRADED", "BLOCKED"):
        assert state in up
    # an explicit authenticated-required state (governed reads need the token)
    assert "AUTH REQUIRED" in up or "Sign-in required" in html
    # honesty: never fabricate when a capability is not live
    low = html.lower()
    assert "fabricat" in low
    assert "claim live" in low  # "does NOT claim live ... capability"


def test_structured_plan_stays_governed():
    html = _html()
    # Calyx can surface a proposed plan (role/agent + confidence + evidence) ...
    assert "Proposed next plan" in html or "Recommended next action" in html
    assert "Confidence:" in html
    # ... but execution stays governed by backend authority — never self-approval
    assert "governed by backend authority" in html


def test_no_hover_only_controls():
    html = _html()
    # primary controls are real buttons (keyboard/tap reachable), not hover reveals
    assert 'id="sendBtn"' in html
    assert 'id="drawerToggle"' in html
    # no control is revealed only on mouse hover
    assert ":hover" not in html
    assert "onmouseover" not in html.lower()
