# BUILD-001-Observation-Engine-Phase1

**Repository:** `jsp1440/orchid-continuum-control-panel`
**Branch:** `claude/github-repo-connection-tjyty2`
**Commit:** `eec27517bf501bfe1c6d25672db0fa4fad77a69f` (local only, not pushed, not signed)
**Status:** Committed locally. Not pushed. Working tree clean.

---

# Executive Summary

Observation Engine Phase 1 adds the evidence acquisition layer beneath
Calyx's Evaluation Engine and Mission Brief. It scans eight real,
already-existing sources in this repository and records what it finds as
immutable observations, reconciled (deduplicated, reaffirmed, or
superseded) rather than duplicated on every scan. It runs entirely
through infrastructure that already existed before this build (the Agent
Registry and Task Queue) — no new run/task/event mechanism was built.
Domains with no real data source (scientific literature, pollinator and
mycorrhizal networks, collaboration) are reported honestly as
unavailable, never fabricated. Evaluation Engine itself was not modified.

# Mission

> Implement Observation Engine Phase 1 — the foundational evidence layer
> feeding Evaluation Engine, the Mission Brief, priority ranking,
> Engineering Memory, and future planning/tool-selection/goal-management
> work. Reuse existing infrastructure; do not invent parallel systems.
> Support only real, verifiable data sources already in this repository;
> return honest nulls for everything else.

# Architectural Decisions

1. **Observation Engine registers as a normal Agent Registry entry
   (`observation_engine`) and runs through the existing
   `POST /api/v1/agents/{key}/run`, rather than getting its own run
   endpoint.** This was the single largest reuse decision — task
   creation, the `pending → running → done/failed` lifecycle, retry
   counting, and event logging were all already built and tested against
   Engineering Auditor. Registering a second `AGENT_RUNNERS` entry gets
   all of that for free.

2. **A new, dedicated router (`/api/v1/observations/*`) was still added**
   for querying observation *records* — this is genuinely new surface,
   since nothing in `agents.py` has a concept of an "observation." New
   API was added only where the underlying concept was actually new.

3. **The observation source catalog (`OBSERVATION_SOURCES`) is a Python
   constant, not a new database table.** A dynamic, DB-backed registry of
   observable sources would have duplicated the Agent Registry's actual
   purpose (a registry of things that *act*) without a demonstrated need
   — Phase 1's eight sources are fixed and code-reviewed, not something
   requiring runtime reconfiguration.

4. **Schema mirrors the existing two-table pattern** (`oc_observations` +
   `oc_observation_events`), the same shape already proven twice
   (`oc_memory_outbox`/`oc_memory_outbox_events`,
   `oc_agent_tasks`/`oc_agent_task_events`) — record table holds current
   state, event table is an append-only log of what happened to each
   record.

5. **`scan_task_id` is a plain column, not a foreign key to
   `oc_agent_tasks`.** A hard FK here would reintroduce the exact
   cross-module-ordering bug found and fixed earlier in this project
   (Engineering Auditor crashing on a fresh database because it assumed a
   table owned by a different module already existed). Traceability is
   kept without the DB-enforced coupling.

6. **Immutability is achieved through evidence-encoded identity, not an
   update operation.** Every observation's `evidence.id` encodes what
   makes it *that specific fact* — a decision's id, a task's id, or (for
   facts that can change state, like database connectivity) the observed
   value itself (`database_reachable` vs. `database_unreachable`). A
   state change is therefore a *new* fact with a *different* identity,
   which supersedes the old one through the same reconciliation logic —
   no "edit" path exists anywhere in this module.

7. **Snapshot-type facts (Evaluation Engine scores, Mission Brief counts)
   are exempt from supersession.** Their evidence id encodes the scan
   itself, so every scan is a distinct, permanent historical data point
   rather than a duplicate of "current state." This is deliberate: both
   Evaluation Engine and Mission Brief are currently stateless (recomputed
   fresh on every call, nothing persisted) — this is the only place either
   gets real history.

8. **Evaluation Engine was not modified.** The regression requirement was
   explicit; rewiring Evaluation to read from `oc_observations` instead of
   live state is real, separate work with its own risk, deferred to a
   named future phase (see "Recommended Next Phase").

9. **Fetch/pure-logic split, mirrored a third time.** `run_observation_engine`
   is the only function that touches the database; every `detect_*`
   function and `reconcile()` are pure, operating on already-fetched data.
   This is the identical pattern already used by `calyx.py`
   (`fetch_state`/`synthesize_brief`) and `evaluation.py` (pure domain
   evaluators) — chosen again for consistency, and because it is what
   made 17 real unit tests possible without any new test infrastructure.

# Files Created

| File | Purpose |
|---|---|
| `observation.py` | Schema (`oc_observations`, `oc_observation_events`), the source catalog, 8 pure `detect_*` functions, `compute_dedup_key`/`reconcile` (pure), `run_observation_engine` (the impure orchestrator), the scheduler stub, and the `/api/v1/observations/*` router |
| `observations.html` | Mission Control dashboard: counts strip, domain coverage grid, filterable observation list, "Observe Now" button |
| `test_observation.py` | 17 pure unit tests covering fact detection and reconciliation, no database dependency |

# Files Modified

| File | Change |
|---|---|
| `agents.py` | Seeds `observation_engine` into `oc_agent_registry`; imports and registers `run_observation_engine` in `AGENT_RUNNERS` |
| `app.py` | Mounts `observation.router`; serves `/observations.html`, gated by the existing `ADMIN_PANEL_TOKEN` dependency |
| `admin.html` | Adds a live "Observation Engine" module card, wired into the existing token-forwarding script |
| `README.md` | Documents the Observation Engine section |

# Database Changes

Two new tables, both created lazily (`CREATE TABLE IF NOT EXISTS`) on
first use, exactly matching every other table in this project — no
migration tooling, no changes to existing tables.

```sql
oc_observations (
    observation_id, source, domain, severity, confidence, status,
    description, evidence JSONB, related_objects JSONB,
    recommended_action, dependencies JSONB, dedup_key, scan_task_id,
    first_observed_at, last_seen_at, created_at, updated_at
)

oc_observation_events (
    event_id, observation_id (FK -> oc_observations),
    event_type, message, created_at
)
```

Additionally, `oc_agent_registry` gains one new seeded row
(`observation_engine`) via the existing `ON CONFLICT DO NOTHING` seed
pattern — no schema change to that table.

# API Endpoints

```
GET   /api/v1/observations/sources     - the fixed source catalog
GET   /api/v1/observations             - list, filterable by domain/source/status/severity
GET   /api/v1/observations/coverage    - per-domain counts + which sources are/aren't available
GET   /api/v1/observations/summary     - active/total counts, last scan time, severity breakdown
POST  /api/v1/agents/observation_engine/run   - the scan trigger (reused endpoint, not new)
```

All gated by the existing `ADMIN_PANEL_TOKEN` dependency.

# UI Changes

- New page `/observations.html`: counts strip, "Observe Now" button with
  last-scan timestamp, domain coverage grid (showing available vs.
  not-yet-available sources per domain), filterable current-observations
  list.
- `admin.html`: one new live module card ("Observation Engine") linking to
  the new page, using the same token-forwarding mechanism as every other
  card.

# Tests Executed

1. `python -c "import ast; ..."` syntax check on all 10 Python files in
   the project.
2. `pytest test_calyx.py test_evaluation.py test_observation.py` — full
   suite.
3. `python -c "import app"` — full application import/route-registration
   check.
4. Live integration test against a real, freshly created local Postgres
   database (created and destroyed within this session, not connected to
   any deployed environment):
   - Admin-token gate enforcement on all new surfaces.
   - **Fresh-database first-run regression test**: `observation_engine`
     run as the very first action against a database with no prior
     tables — this is the exact failure class found and fixed earlier
     this session in Engineering Auditor; verified it does not recur here.
   - Domain coverage endpoint correctness (scientific/collaboration
     reported honestly as zero/unavailable, not fabricated).
   - Content of a "no data" observation inspected directly — confirmed it
     states "no data available" with `confidence: low`, not a fabricated
     number.
   - Second scan immediately after the first: state-type sources fully
     reaffirmed (zero new inserts); snapshot-type sources correctly
     inserted fresh (accumulating history).
   - Real state-change test: created a decision, moved it to
     `under_review` (observation created, citing the real decision id),
     then `accepted` (re-scan correctly superseded the old observation;
     history preserved under `status=superseded`, not deleted).
   - Full regression pass: Engineering Memory API, Evaluation Engine's
     `mission-brief` response shape, Agent Registry (now correctly
     listing both agents), admin-token enforcement on every existing
     gated route, all five HTML pages, and Engineering Auditor's own run
     (unaffected by the new registry entry).

# Test Results

- **47/47 pytest tests passed** (31 pre-existing, unmodified + 17 new for
  Observation Engine — zero existing tests required modification).
- **All live integration checks passed**, including the fresh-database
  regression test, dedup verification, and the real state-change/
  supersession test.
- **Zero regressions** detected across Engineering Memory, Evaluation
  Engine, Agent Registry, Mission Brief, admin-token enforcement, or any
  existing route.

# Commit Hash

```
eec27517bf501bfe1c6d25672db0fa4fad77a69f
```

Local only. Not pushed. Not signed (per instruction). 9 commits total on
`claude/github-repo-connection-tjyty2`, none pushed to
`origin/claude/github-repo-connection-tjyty2`.

# Current Architecture Diagram

```
                    Humans (Mission Control UI)
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │              Mission Control                │
        │  admin.html / engineering-memory.html /       │
        │  agents.html / calyx.html / observations.html  │
        └─────────────────────┬───────────────────────┘
                              │  ADMIN_PANEL_TOKEN gate (admin.py)
                              ▼
        ┌─────────────────────────────────────────────────┐
        │                     Calyx (calyx.py)                │
        │   Mission Brief · /ask (grounded Q&A) · /evaluate     │
        └───────┬───────────────────┬──────────────────┬──────┘
                │                   │                    │
                ▼                   ▼                    ▼
   ┌────────────────────┐ ┌──────────────────┐ ┌─────────────────────┐
   │  Evaluation Engine    │ │ Engineering Memory │ │  Observation Engine   │
   │  (evaluation.py)       │ │   (memory.py)       │ │   (observation.py)     │
   │  4 domain scores,        │ │  decisions,           │ │  NEW THIS BUILD          │
   │  ranked priorities         │ │  relationships,         │ │  oc_observations,          │
   │  - reads live state,         │ │  links, lifecycle          │ │  oc_observation_events       │
   │    UNCHANGED this build        │ │                              │ │  - scans 8 real sources,       │
   └────────────┬────────────┘ └──────────┬───────────┘ │    reconciles, never          │
                │                          │              │    duplicates or edits          │
                └──────────┬───────────────┘              └────────────┬─────────────────────┘
                           │                                            │
                           ▼                                            ▼
                ┌───────────────────────────────────────────────────────────────┐
                │              Agent Registry + Task Queue (agents.py)              │
                │   oc_agent_registry: engineering_auditor, observation_engine        │
                │   oc_agent_tasks / oc_agent_task_events                              │
                │   AGENT_RUNNERS = {                                                    │
                │     "engineering_auditor": run_engineering_auditor,                     │
                │     "observation_engine": run_observation_engine,   <- NEW               │
                │   }                                                                        │
                └───────────────────────────────────────────────────────────────────────┘
                           │
                           ▼
                ┌───────────────────────────────┐
                │  Brain Outbox (memory.py)        │
                │  oc_memory_outbox / _events         │
                │  (unrelated to this build,             │
                │   unaffected)                            │
                └───────────────────────────────┘
```

Note: Observation Engine reads Evaluation Engine's and Calyx's output
(via deferred imports inside `run_observation_engine`) to produce
snapshot observations, but neither Evaluation Engine nor Calyx's Mission
Brief logic read from Observation Engine yet — the arrow is one-directional
in this phase, not a closed loop.

# What Observation Engine Can Do Today

- Scan and record real facts from 8 sources: pending Engineering Memory
  decisions, registered agents, failed task runs, open findings, a score
  snapshot per Evaluation Engine domain, a Mission Brief counts snapshot,
  the deployed commit (when running on Render), and a live database
  connectivity check.
- Deduplicate correctly — repeated scans never create duplicate rows for
  facts that haven't changed.
- Detect state changes and supersede stale observations while preserving
  their history (verified against a real decision moving from
  `under_review` to `accepted`).
- Accumulate permanent history for two components (Evaluation Engine,
  Mission Brief) that previously had none.
- Report honest zero/unavailable coverage for scientific literature,
  pollinator networks, mycorrhizal networks, and collaboration — with no
  fabricated data anywhere in that path.
- Run manually via Mission Control's "Observe Now" button or directly via
  `POST /api/v1/agents/observation_engine/run`.

# What It Cannot Yet Do

- **Cannot run on a schedule.** `scheduled_scan_stub()` exists and is
  documented but deliberately raises `NotImplementedError` — there is no
  Scheduler component anywhere in this project yet (a gap already
  identified in the AI Fabric architecture document).
- **Does not feed Evaluation Engine.** Evaluation still reads live state
  directly, not `oc_observations`. Observation Engine currently runs
  alongside Evaluation Engine, not underneath it.
- **Cannot observe anything outside this one repository.** No access to
  `orchid-continuum-frontend`, `orchid-calyx-backend`, or Brain — all
  "repository metadata" is limited to this repo's own deployed commit.
- **Has no notion of severity beyond what its source already states.**
  Observation Engine deliberately does not re-judge urgency (that stays
  Evaluation Engine's job) — it carries forward the source record's own
  severity/state, nothing more.
- **Has no automated approval or action.** Purely observational, exactly
  as scoped — no writes anywhere except its own observation records.

# Remaining Dependencies

- A real Scheduler component (named but unbuilt in the AI Fabric
  architecture document) is required before "Observe Now" can become
  "observe automatically."
- Multi-repository access (`orchid-continuum-frontend`,
  `orchid-calyx-backend`, Brain) is required before "repository metadata"
  or cross-repo observation can mean anything beyond this one repo.
- A real literature/pollinator/mycorrhizal/collaboration data source must
  exist in *some* repository before those domains can produce anything
  but an honest null — this build does not change that.

# Recommended Next Phase

**Observation Engine Phase 2: wire Evaluation Engine to consume
`oc_observations` instead of live state.** This is the natural next step
implied by the original mission statement ("without observation, the
Evaluation Engine has no evidence to evaluate") and was explicitly
deferred here to keep this phase's regression surface small. Concretely:
replace `evaluate_engineering`/`evaluate_scientific`/etc.'s direct reads
of `calyx.fetch_state()` with reads of active `oc_observations` rows
filtered by domain, and re-run the full Evaluation test suite to confirm
identical scoring behavior on the same underlying data (a true "same
output, different input path" refactor, not a behavior change).

Second priority: a minimal real Scheduler (even a single cron-style
env-var-driven trigger calling the existing `observation_engine` run
endpoint) — this closes the largest gap named in "What It Cannot Yet Do."

# Risks

- **Reconciliation correctness depends entirely on evidence-id design
  per source.** Getting a source's evidence-id scheme wrong (e.g.
  encoding scan-uniqueness into a fact that should be deduplicated, or
  vice versa) silently produces either duplicate spam or lost history.
  This was tested carefully for all 8 sources in this build, but any
  *new* source added later needs the same care — the distinction (state
  vs. snapshot) is a design judgment call each time, not automatically
  enforced by the schema.
- **`oc_observations` will grow unboundedly for snapshot sources** since
  they're never pruned or archived — acceptable for Phase 1 volume, but
  worth a retention policy before Evaluation Engine scans run frequently
  or automatically (ties directly to the Scheduler dependency above).
- **No FK constraint on `scan_task_id`** (a deliberate choice, explained
  above) means it's possible for that value to reference a task that no
  longer exists if `oc_agent_tasks` rows are ever pruned in the future —
  low risk today since nothing prunes that table, but worth remembering
  if retention policies are added later.
- **This is the ninth build in this session's history without a push to
  the remote branch.** Purely a process risk, not a technical one, but
  worth surfacing: local-only commits accumulate real, uncommitted-to-shared-history
  risk (machine loss, session loss) the longer they stay unpushed.

# Future Opportunities

- Once Phase 2 (Evaluation reading from Observations) lands, Observation
  Engine becomes the actual, literal evidence trail behind every
  Evaluation score and every Calyx recommendation — directly satisfying
  the "traceable, no opaque scoring" principle already established for
  Evaluation Engine, one level deeper.
- The domain-coverage endpoint is a ready-made foundation for a public
  (or Mission-Control-facing) "what does Calyx actually know" status
  page — useful both for internal trust-building and for prioritizing
  which future data source (literature, pollinator network, etc.) to
  build next, based on what's currently a visible, honest gap rather than
  a hidden one.
- The reconciliation pattern proven here (evidence-id-encodes-identity)
  is general enough to become the standard mechanism for any future
  "detect real-world facts, don't duplicate, supersede on change" need in
  this project — worth documenting as a reusable pattern rather than
  something Observation Engine alone owns.

# Open Questions

- Should snapshot-type observations (Evaluation/Mission Brief history)
  eventually get a retention/archival policy, and if so, at what age or
  volume threshold?
- When Evaluation Engine is rewired to read from Observations (Phase 2),
  should Observation Engine's scan become a prerequisite step *inside*
  `POST /evaluate`, or should they remain two independently-triggered
  actions as they are today?
- Is `RENDER_GIT_COMMIT` the right signal for "repository metadata," or
  should this eventually read from a real GitHub API call once
  `AUDIT_GITHUB_TOKEN`-style credentials exist (as discussed in earlier
  architecture work on repo status monitoring)?
- Should the fixed `OBSERVATION_SOURCES` catalog remain a Python constant
  indefinitely, or is there a real, demonstrated need (not yet observed)
  for it to become configurable without a code change/deploy?
