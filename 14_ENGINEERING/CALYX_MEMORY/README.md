# Calyx Memory System

This directory is a scaffold for Calyx's long-term memory system, separate
from the Engineering Memory decision log (`memory.py`, `oc_memory_decisions`)
and the Observation Engine's evidence store (`observation.py`,
`oc_observations`).

No code lives here yet. This file exists to reserve the location and record
the distinction between the memory systems that already exist and the one
this directory is meant for:

- **Engineering Memory** (`memory.py`) records human/AI *decisions* - what
  was decided, why, and its lifecycle status.
- **Observation Engine** (`observation.py`) records *evidence* - immutable,
  reconciled facts detected from real system state.
- **Evaluation Engine** (`evaluation.py`) records *interpretation* - scores
  and prioritized signals derived from Engineering Memory and Observations.
- **Calyx Memory** (this directory) is intended for Calyx's own reasoning
  memory - a record of what Calyx has been asked, what it has concluded, and
  how those conclusions relate to the evidence and decisions above. This is
  distinct from all three existing systems and has not been designed or
  implemented yet.

Nothing in this directory is wired into `app.py` or any router. It has no
tables, no API, and no UI.
