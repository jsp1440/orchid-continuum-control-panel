# Research Intelligence & Innovation Management

This directory records the planned Research Intelligence & Innovation Management domain for the Orchid Continuum control panel and Brain.

## Purpose

Research Intelligence & Innovation Management treats major scientific and engineering innovations as first-class research objects. Each important innovation should receive a persistent Orchid Continuum Innovation identifier and should accumulate evidence, implementation history, publication potential, grant relevance, downstream effects, and lifecycle status.

## Core subsystems

- **OCI records** — persistent Orchid Continuum Innovation identifiers such as `OCI-0001`.
- **Innovation Register** — master catalog of innovations, ideas, methods, datasets, workflows, and architectural concepts.
- **Innovation Provenance & Research Lifecycle (IPRL)** — longitudinal record of an innovation from inception through implementation, validation, publication, grants, and downstream derivatives.
- **Publication Opportunities Register (POR)** — publishable methods, datasets, systems, analyses, or conceptual contributions surfaced by the Brain.
- **Evidence Validation & AI Benchmarking Framework (EVAB / OCAB)** — evaluates scientific claims and AI systems against curated evidence, validates citations, detects hallucinations, measures uncertainty calibration, and records knowledge gaps.
- **Conversation Innovation Auditor** — end-of-session audit process to identify new innovations, architectural decisions, publication opportunities, grant implications, literature needs, and follow-up actions.
- **Grant Opportunity Tracker** — links innovations and research products to funding opportunities.

## First-class research object principle

Everything that contributes to scientific discovery should be representable as a provenance-rich research object connected within a unified knowledge graph:

- taxon
- publication
- dataset
- claim
- hypothesis
- knowledge gap
- conversation
- software module
- benchmark
- grant
- innovation

OCI records are the canonical object type for innovations.

## Initial OCI candidates

- `OCI-0001` — Research Intelligence & Innovation Management domain
- `OCI-0002` — Evidence Validation & AI Benchmarking Framework / Orchid Continuum AI Benchmark
- `OCI-0003` — Innovation Provenance & Research Lifecycle
- `OCI-0004` — Publication Opportunities Register
- `OCI-0005` — Conversation Innovation Auditor
- `OCI-0006` — Innovation-as-first-class-research-object architecture

## Implementation status

This directory is the first persistent repository scaffold for the domain. It is intentionally separate from the existing Engineering Memory, Observation Engine, Evaluation Engine, and Calyx Memory systems until the API and database schema are wired explicitly.
