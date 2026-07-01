# orchid-continuum-control-panel
Control panel dashboard for Orchid Continuum harvesters and database status

## Engineering Memory / Brain Outbox

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
