# Orchid Continuum Control Panel — Coding Agent Instructions

This repository is Mission Control / operational observability. It is not the canonical engineering mission orchestrator.

Before implementation:
1. Inspect current `main`, related issues/PRs, and current live-operational contracts.
2. Read the relevant Brain mission/architecture record.
3. Classify work `NEW`, `CONTINUE`, `CONVERGE`, `SUPERSEDE`, or `ALREADY_DONE`.
4. Prefer real telemetry and existing Calyx backend interfaces over duplicated status logic.

Rules:
- Never fabricate service health, agent status, counts, runtime state, or completion.
- Missing or unreachable telemetry must be represented explicitly.
- Do not create a second coding-agent queue/orchestrator when the canonical Calyx durable mission substrate already owns engineering missions.
- Keep agent registry/observation features reviewable and provenance-backed.
- Add focused tests and run the relevant pytest/application validation.
- Distinguish external-service/network failure from application failure.
- Stop automatic repair after three failed iterations on the same deterministic failure class and escalate.

Default implementation output is a draft PR with convergence classification and validation evidence.

Do not merge, deploy, mutate production DB/KG, activate taxonomy, publish science, expose credentials, spend funds, force-push, or delete branches/repos without required owner authorization.
