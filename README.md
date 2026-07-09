# orchid-continuum-control-panel
Control panel dashboard for Orchid Continuum harvesters and database status

## Orchid Continuum Mission Control

Mission Control is the password-protected administrative landing layer for
the Orchid Continuum - not a single tool. **Engineering Memory is one
module inside it**, not the product itself. The landing page shows module
cards for what's live today (Engineering Memory, Brain Outbox, Health
Check, Brain/DB Status, Atlas, AI Agents) plus disabled placeholders for
what's planned (GitHub/Repo Status, Website Health, Grants Tracker,
Partner Follow-ups, Research Station, Conservation Ops, Education/OCU).
More modules will be added to this same landing page over time; none of
them require rebuilding the gate or the layout.

This repo serves its own Mission Control UI directly - it is not a separate
frontend project and does not currently depend on the main Orchid Continuum
website repo. FastAPI serves a handful of standalone HTML pages itself
(this is the same pattern already used for `/atlas.html`); there is no
separate JavaScript framework or build step. The public Orchid Continuum
website is a different system and is not required for Mission Control to
work. Connecting Mission Control to that site later (e.g. linking to it, or
embedding it) is a future step, not a dependency of this one.

The Mission Control landing page is served at `/admin.html` (the route name
is unchanged; only the user-facing name is "Mission Control") and links to
the internal tools below. It is **not** linked from anywhere on the public
site or from any other page in this repo.

### Enabling Mission Control

1. Set the `ADMIN_PANEL_TOKEN` environment variable to a long random string
   (e.g. `openssl rand -hex 32`). This is the only credential - there is no
   username, no database of users, no password reset flow.
2. If `ADMIN_PANEL_TOKEN` is not set, every admin route (`/admin.html`,
   `/engineering-memory.html`, and everything under `/api/v1/memory/*`)
   returns `503 Admin panel is disabled` - it does **not** fall open to the
   public. There is no hard-coded password anywhere in this repo.

### Accessing it

Visit `/admin.html?token=<your ADMIN_PANEL_TOKEN>`. The page carries the
token forward automatically to the pages it links to (Engineering Memory,
Brain Outbox). API calls from those pages send the token as an
`Authorization: Bearer` header; the browser-facing pages also accept it as
a `?token=` query parameter, since a plain link/bookmark can't set custom
headers.

Current auth is a **temporary shared-token gate** - a single secret string,
not individual logins. It exists only to stop the administrative surface
from being wide open while a real system is designed.

### Security caveats

This is explicitly a **placeholder gate**, not a real authentication
system:

- It's a single shared secret, not per-user accounts - anyone with the
  token has full access, and there's no way to revoke one person's access
  without rotating the token for everyone.
- The token can appear in browser history and server access logs because
  it's accepted as a URL query parameter (needed so the plain HTML pages
  work without custom headers). Treat the token as sensitive and rotate it
  if you suspect it has leaked.
- There is no session expiry, no rate limiting, no audit log of who used
  the token.
- Do not reuse this token as a password anywhere else.

### Future plan for real authentication

This gate exists to stop the admin surface from being wide open while a
real system is designed - it is intentionally not more than that. A future
iteration should replace it with per-user accounts (or SSO), real sessions,
and role-based permissions, consistent with the identity/permission model
already discussed for the Engineering Operations Center. That is a
deliberately separate, larger piece of work, not part of this change.

## Engineering Memory / Brain Outbox

One module within Mission Control (see above), not the whole product.
Reachable from the Mission Control landing page, not just as a standalone
page. Gated by the same `ADMIN_PANEL_TOKEN` described above.

The Orchid Continuum Brain is a separate system, and this Control Panel does
not have live access to it today. Rather than let engineering decisions live
only in chat transcripts, this repo records them locally and prepares them
for later synchronization once a Brain sync integration exists.

- **Control Panel is the local system of record for engineering decisions**
  until Brain sync exists. Decisions are written to `oc_memory_decisions` in
  this app's own database immediately, in full, before any sync is attempted.
- **The Brain Outbox (`oc_memory_outbox`) is a durable queue, not a live
  Brain connection.** Queueing a decision for sync writes an outbox row with
  `sync_status='pending'` and records every state transition in
  `oc_memory_outbox_events`. Nothing about recording a decision depends on
  the Brain being reachable.
- **Nothing is lost if the Brain is unavailable.** If `BRAIN_SYNC_ENDPOINT`
  is not set, queueing a decision leaves the outbox entry `pending` and logs
  a `sync_skipped` event - no error, no data loss, the system keeps working
  locally. If the endpoint is set but the request fails, the entry moves to
  `failed` with the error recorded, and can be retried later.
- **Future Brain integration only needs an adapter that drains pending
  outbox messages** - something that polls
  `GET /api/v1/memory/outbox?sync_status=pending`, POSTs each payload to the
  real Brain API, and calls `mark-sent` / `mark-confirmed` / `mark-failed`
  accordingly. No changes to how decisions are recorded are required when
  that adapter is built.

### Configuration

| Env var | Required | Purpose |
|---|---|---|
| `BRAIN_SYNC_ENDPOINT` | No | URL to POST queued decisions to. If unset, sync attempts are skipped and messages stay `pending`. |
| `BRAIN_SYNC_TOKEN` | No | Bearer token sent with sync requests, if `BRAIN_SYNC_ENDPOINT` is set. |

No Brain credentials are hard-coded anywhere in this repo.

### Decision lifecycle

Decisions move through a small, explicit state machine - only the listed
transitions are allowed; anything else is rejected with a 400 explaining the
valid next states:

```
proposed ──► under_review ──► accepted ──► implemented ──┬──► deprecated
    │                              │                        └──► superseded
    └──────────────► rejected ◄────┘
```

`deprecated` and `accepted` may also go directly to `superseded`.
`superseded` and `rejected` are terminal.

### Relationships and links

Decisions can be connected to each other and to external artifacts:

- **`oc_memory_decision_relationships`** - typed edges between decisions
  (`supersedes`, `parent_of`, `conflicts_with`, `related_to`). Stored once,
  in one direction; the reverse view is a query, not a duplicated column.
- **`oc_memory_decision_links`** - references from a decision to external
  artifacts (`task`, `finding`, `commit`, `pull_request`, `release`,
  `document`, `external_url`).

Decisions also carry a `governance_refs` field (a plain JSON array) for
citing standards or policy - it's an unvalidated placeholder today, since
there is no Governance/Constitution engine yet, and costs nothing to carry
forward.

### API

```
POST   /api/v1/memory/decisions
GET    /api/v1/memory/decisions
GET    /api/v1/memory/decisions/{decision_id}
PATCH  /api/v1/memory/decisions/{decision_id}/status
POST   /api/v1/memory/decisions/{decision_id}/queue-brain-sync

POST   /api/v1/memory/decisions/{decision_id}/relationships
GET    /api/v1/memory/decisions/{decision_id}/relationships
POST   /api/v1/memory/decisions/{decision_id}/links
GET    /api/v1/memory/decisions/{decision_id}/links

GET    /api/v1/memory/outbox
POST   /api/v1/memory/outbox/{outbox_id}/mark-sent
POST   /api/v1/memory/outbox/{outbox_id}/mark-confirmed
POST   /api/v1/memory/outbox/{outbox_id}/mark-failed
```

A minimal UI is served at `/engineering-memory.html` showing recent
decisions with their lifecycle status (changeable inline), an expandable
details panel per decision for relationships and links, and
pending/failed/confirmed Brain sync status.

## AI Agents (Engineering Auditor - Phase 1)

This is the minimum substrate for exactly one grounded AI agent - not the
full AI Fabric design (no Model Router, Event Bus, Evaluation Engine, or
Scheduler yet). It exists to prove the pattern with one real agent before
building more.

- **`oc_agent_registry`** - catalog of agent definitions (key, name,
  purpose, lifecycle state, enabled flag). Seeded with one entry:
  `engineering_auditor`.
- **`oc_agent_tasks`** / **`oc_agent_task_events`** - a durable run queue
  for agent executions, following the exact same pattern as the Brain
  Outbox (`oc_memory_outbox`/`oc_memory_outbox_events`): one row per run,
  a status field, an attempt count, and an append-only event log.
- **`oc_agent_findings`** - what an agent produces. Findings are **draft,
  reviewable records** - an agent never modifies Engineering Memory
  decisions directly. A human (or a future approval workflow) acknowledges
  or resolves a finding explicitly.

**Engineering Auditor** reads `oc_memory_decisions` and
`oc_memory_decision_links`: any decision with `status = 'implemented'` and
zero linked commits/PRs/releases/documents/tasks gets a
`missing_implementation_link` finding. On every run, findings for
decisions that are no longer missing links are automatically marked
`resolved` - the same reconciliation approach used elsewhere in this
project's monitoring design (open new, auto-resolve what's no longer
true).

Runs are triggered manually today (`POST /run` or the "Run Now" button in
the UI) - there is no scheduler in Phase 1.

### API

```
GET    /api/v1/agents
GET    /api/v1/agents/{agent_key}
POST   /api/v1/agents/{agent_key}/run
GET    /api/v1/agents/{agent_key}/tasks
GET    /api/v1/agents/{agent_key}/findings
PATCH  /api/v1/agents/findings/{finding_id}
```

All gated by the same `ADMIN_PANEL_TOKEN` described above. A minimal UI is
served at `/agents.html` showing each registered agent, its last run,
"Run Now," and its findings with acknowledge/resolve actions.

## Calyx (Alpha)

Calyx is the Orchid Continuum's Project Director and Chief Scientific
Intelligence - not another entry in the Agent Registry, but the directing
intelligence that reads across it. This is **Phase Alpha**: enough to be
operational, not full autonomy.

**What Calyx Alpha does**: reads Engineering Memory, the Task Queue, the
Agent Registry, Engineering Findings, and the Brain Outbox, and produces a
Mission Brief answering what's healthy, broken, blocked, waiting for
review, and closest to completion - plus a single recommended next action,
citing the specific decision/finding/task it's based on, and naming which
registered agent (if any) is suited to it. If no real agent fits, Calyx
says so rather than inventing one.

**What Calyx Alpha does not do**: write to any table, execute code, deploy
anything, or approve its own work. It observes, analyzes, recommends, and
answers questions - that's the entire Phase Alpha scope. Turning a
significant recommendation into a drafted Engineering Memory decision is
explicitly deferred to a later phase.

**On "conversation"**: there is no LLM call in this phase, and no Model
Router (that component doesn't exist yet, per the AI Fabric architecture
document's Operational Readiness section). `/api/v1/calyx/ask`
matches a question against the same set of question patterns the Mission
Brief already answers, and returns a template filled in from real query
results. It's grounded, testable, and honest about its limits - not a
general-purpose chat model wearing Calyx's name.

Every table Calyx reads already exists and is owned by Engineering Memory
or the Agent substrate (`oc_memory_decisions`, `oc_memory_outbox`,
`oc_agent_registry`, `oc_agent_tasks`, `oc_agent_findings`) - **Calyx
Alpha introduces zero new database tables.**

### API

```
GET   /api/v1/calyx/mission-brief
POST  /api/v1/calyx/ask
POST  /api/v1/calyx/evaluate
```

Gated by `ADMIN_PANEL_TOKEN`. A dashboard is served at `/calyx.html`
showing the full Mission Brief, domain scores, ranked priorities, and an
"Ask Calyx" box with the seven example questions as one-click buttons.

### Testing

`test_calyx.py` and `test_evaluation.py` cover the pure synthesis,
scoring, and intent-matching logic without touching a database
(`fetch_state()` is the only function in `calyx.py` that opens a
connection; everything else, including all of `evaluation.py`, is a pure
function over already-fetched data, which is what makes it unit-testable
at all). Run with `pytest` after installing `requirements-dev.txt`.

## Evaluation Engine (Calyx Phase 2)

Not an AI model - a deterministic, explainable scoring and prioritization
layer (`evaluation.py`) that turns Calyx from "I know what exists" into
"I know what should happen next."

**Four domains, evaluated independently**, each producing a named score
plus an itemized signal list (the score's exact arithmetic, not an opaque
number):

| Domain | Score | Real data source in this repo |
|---|---|---|
| Engineering | Engineering Health Score | Open findings, failed agent runs, failed Brain Outbox syncs, stale decision reviews |
| Scientific | Scientific Opportunity Score | Taxonomy/image coverage (`orchid_taxonomy`/`images`, if present) - literature/pollinator/mycorrhiza/conservation gaps have **no data source yet** and are reported as such, not estimated |
| Mission Progress | Mission Progress Score | Engineering Memory decision lifecycle status and relationships |
| Collaboration | Collaboration Opportunity Score | **No data source exists anywhere in this repository** - always reports `score: null`, never a fabricated number |

**Priority ranking**: every signal from every domain becomes a ranked
priority item with `priority` / `reason` / `evidence` (a real record
citation) / `expected_impact` / `dependencies` (derived from
`parent_of` relationships) / `suggested_agent` (only when a real
`agent_key` exists on the underlying record) / `suggested_tool` (a
deterministic mapping to Claude Code, Python, SQL, Atlas, Harvesters, the
Literature Pipeline, or a not-yet-built future system - never a hardcoded
AI provider) / `confidence`. Ranking weight is `severity x domain`, stated
plainly in code, not a black box.

**The one write path**: `GET /mission-brief` and `POST /ask` remain pure
reads with no side effects. `POST /evaluate` is the deliberate action
(mirroring "Run Now" for agents) that also proposes Engineering Memory
decisions for the top critical/high-priority items - through
`memory.create_decision()`, the same entry point every other creator
uses, always at status `proposed`, capped at 3 per call, and deduplicated
by an evidence marker embedded in the decision's `context` so repeated
calls don't spam duplicate proposals.

`/api/v1/calyx/ask`'s answers are enriched with the same priority data
when available - asking "what should we work on today" now returns
reason, evidence, expected impact, suggested agent/tool, dependencies, and
confidence in one answer, falling back to Phase Alpha's simpler format
only when there is nothing to evaluate yet.

## Observation Engine (Phase 1)

The evidence acquisition layer beneath Evaluation and the Mission Brief -
not another agent's private log, but a shared, immutable record of facts
Calyx has directly observed. **Runs through the existing Agent Registry
and Task Queue, not a new run path**: it's registered as agent_key
`observation_engine`, executed via the same
`POST /api/v1/agents/observation_engine/run` every other agent uses.

**Eight sources, Phase 1** - all real, all already in this repository:
Engineering Memory (decisions awaiting action), Agent Registry, Task
Queue (failed runs), Agent Findings (open), Evaluation Engine (a score
snapshot per domain), Mission Brief (a counts snapshot), repository
metadata (`RENDER_GIT_COMMIT`, honestly "unknown" if unset), and a direct
database connectivity check Calyx performs itself. Scientific literature,
pollinator/mycorrhizal networks, and collaboration have **no data source
anywhere in this repository** and are never fabricated - `GET
/api/v1/observations/coverage` reports them plainly as unavailable.

**Two kinds of fact, one reconciliation mechanism**: "state" facts
(pending decisions, failed tasks, open findings, registry entries,
repository commit, health) encode their current value into the
observation's evidence id, so a changed fact is naturally a *different*
id - reconciliation supersedes the old observation and inserts the new
one, with the old row preserved (status `superseded`), never deleted or
edited. "Snapshot" facts (Evaluation Engine's scores, Mission Brief's
counts) encode the scan itself into the evidence id, so every scan
accumulates as permanent history instead of being deduplicated -
Evaluation and Mission Brief are both currently stateless, so this is
the only place either gets a real history at all.

**Evaluation Engine is untouched.** Observation Engine is built alongside
it in Phase 1, not wired underneath it - rewiring Evaluation to read from
`oc_observations` instead of live state is real, separate work for a
later phase.

### API

```
GET   /api/v1/observations/sources
GET   /api/v1/observations              (filter by domain/source/status/severity)
GET   /api/v1/observations/coverage
GET   /api/v1/observations/summary
POST  /api/v1/agents/observation_engine/run   (the scan trigger - reused, not new)
```

Gated by `ADMIN_PANEL_TOKEN`. A dashboard is served at `/observations.html`
showing current observations, domain coverage, counts, last scan, and a
manual "Observe Now" button.

### Testing

`test_observation.py` covers fact detection and reconciliation logic
without touching a database - the same fetch/pure-logic split already
used by `calyx.py` and `evaluation.py`. `fetch_state`-equivalent database
reads happen only inside `run_observation_engine`; every `detect_*`
function and `reconcile()` itself take already-fetched data and are
fully unit-testable.

## Mission Control Operational Status (BUILD-INFRA-003)

Mission Control now has a single operational inventory endpoint:

```
GET /api/v1/mission-control/status
```

It is gated by the same `ADMIN_PANEL_TOKEN` as the rest of Mission Control.
The endpoint reports the repository-local operational state for existing
modules, science pipelines, homepage data-flow surfaces, schedule
recommendations, readiness score, deployment flags, and the next five
recommended builds. The landing page (`/admin.html`) consumes this endpoint
directly so the first screen shows how many modules are operational, how
many are partial, how many science pipelines are waiting for implementation,
and the current readiness score.

This inventory is deliberately evidence-backed:

- Existing modules cite concrete files, routes, environment variables, and
  tables already present in this repository.
- Missing science systems such as literature, pollinators, mycorrhiza,
  knowledge graph, conservation, and education are reported as
  `pipeline_not_yet_implemented`, not approximated.
- Database table counts are included only when `DATABASE_URL` is reachable;
  otherwise the endpoint reports the database blocker explicitly.
- Deployment flags are generated from this repository's actual change
  shape: backend deployment required, frontend deployment not required,
  database migration not required, Render configuration change not required.

The pure synthesis logic is covered by `test_operational.py`.
