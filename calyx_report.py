# FILE: calyx_report.py
# Calyx Report — Mission Control article-generation workspace.
#
# Architecture:
#   • When JOURNALISM_ENGINE_URL is set and the backend is reachable, all
#     evidence-preview and generation calls are proxied to the Journalism
#     Engine (orchid-calyx-backend PR #174).
#   • When the backend is absent or unreachable, the workspace falls back to
#     LIMITED-EVIDENCE MODE — a clearly labeled structured evidence summary
#     drawn from available Calyx / observation-engine data.  No prose is
#     fabricated; the output is explicitly NOT a completed article.
#
# Runtime capability check:
#   check_journalism_engine_capability() probes GET {JOURNALISM_ENGINE_URL}/capability
#   at request time instead of relying on a hard-coded flag.
#
# Backend contract (orchid-calyx-backend PR #174):
#   GET  .../capability
#   POST .../evidence-preview  {topic, audience, use_full_continuum}
#   POST .../generate          {publication, topic, custom_topic, audience,
#                               word_count, citation_mode, confidence_mode,
#                               optional_sections, visuals, use_full_continuum}

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from admin import require_admin_token

router = APIRouter(
    prefix="/api/v1/calyx-report",
    tags=["Calyx Report"],
    dependencies=[Depends(require_admin_token)],
)

# ---------- backend configuration ----------
# Set JOURNALISM_ENGINE_URL to the base URL of the Calyx Journalism Engine
# (orchid-calyx-backend PR #174).  Leave unset to operate in limited-evidence
# mode.  Example: JOURNALISM_ENGINE_URL=https://calyx-backend.example.com
JOURNALISM_ENGINE_URL: str = os.getenv("JOURNALISM_ENGINE_URL", "").rstrip("/")

PUBLICATIONS = {
    "fcos_newsletter": "Five Cities Orchid Society Newsletter",
}

TOPICS = {
    "conservation_survey": "Conservation projects around the world",
    "conservation_spotlight": "Conservation spotlight",
    "new_species": "New species and taxonomy",
    "pollination": "Pollination",
    "mycorrhizae": "Mycorrhizae and germination",
    "habitat_climate": "Habitat and climate",
    "research_trends": "Research trends",
    "custom": "Custom topic",
}

AUDIENCES = {
    "general_members": "General members",
    "advanced_growers": "Advanced growers",
    "researchers": "Researchers and academics",
    "public": "General public",
}

CITATION_MODES = {"none", "source_list", "inline", "numbered_endnotes"}
CONFIDENCE_MODES = {"exploratory", "standard", "high_confidence_only"}

DEFAULT_WORD_COUNT = 1000
MIN_WORD_COUNT = 200
MAX_WORD_COUNT = 3000

# ---------- request / response models ----------


class ReportRequest(BaseModel):
    publication: str = Field(default="fcos_newsletter")
    topic: str = Field(default="conservation_survey")
    custom_topic: Optional[str] = Field(default=None)
    audience: str = Field(default="general_members")
    word_count: int = Field(default=DEFAULT_WORD_COUNT, ge=MIN_WORD_COUNT, le=MAX_WORD_COUNT)
    citation_mode: str = Field(default="source_list")
    confidence_mode: str = Field(default="standard")
    optional_sections: list[str] = Field(default_factory=list)
    visuals: list[str] = Field(default_factory=list)
    use_full_continuum: bool = Field(default=False)


# ---------- runtime capability check ----------


def check_journalism_engine_capability() -> dict[str, Any]:
    """Probe the Journalism Engine backend capability endpoint at runtime.

    Returns a dict with an ``available`` key (bool) and optional metadata
    returned by the backend.  Falls back gracefully if the URL is unset or
    the probe fails — callers must never raise on this return value.

    Backend contract (orchid-calyx-backend PR #174):
      GET  {JOURNALISM_ENGINE_URL}/capability
      →  {version: str, supported_topics: [...], supported_audiences: [...]}
    """
    url = JOURNALISM_ENGINE_URL
    if not url:
        return {"available": False, "reason": "JOURNALISM_ENGINE_URL not configured"}
    try:
        req = urllib.request.Request(
            f"{url}/capability",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read())
                data["available"] = True
                return data
            return {
                "available": False,
                "reason": f"capability endpoint returned HTTP {resp.status}",
            }
    except Exception:
        return {"available": False, "reason": "Journalism Engine not reachable"}


# ---------- backend proxy ----------


def _call_journalism_backend(
    method: str,
    path: str,
    body: Optional[dict[str, Any]] = None,
    auth_token: str = "",
) -> dict[str, Any]:
    """Make an HTTP call to the Journalism Engine backend.

    Raises ``RuntimeError`` (with an HTTP-status message) if the call fails,
    so callers can surface a meaningful error to the client.

    Backend contract (orchid-calyx-backend PR #174):
      POST .../evidence-preview
        body: {topic, audience, use_full_continuum}
        → {mode, observation_count, taxonomy_families_covered,
           taxonomy_species_covered, recent_finding_summaries, data_sources,
           project_table, warnings, insufficient_evidence, generated_at}

      POST .../generate
        body: {publication, topic, custom_topic, audience, word_count,
               citation_mode, confidence_mode, optional_sections, visuals,
               use_full_continuum}
        → {markdown, word_count, evidence_mode, citations, project_table,
           warnings, insufficient_evidence, confidence_score, generated_at}
    """
    base = JOURNALISM_ENGINE_URL
    full_url = f"{base}{path}"
    headers: dict[str, str] = {"Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"******"
    encoded: Optional[bytes] = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        encoded = json.dumps(body).encode()
    http_req = urllib.request.Request(
        full_url, data=encoded, headers=headers, method=method.upper()
    )
    try:
        with urllib.request.urlopen(http_req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"Journalism Engine returned HTTP {exc.code}: {body_text}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Journalism Engine call failed: {exc}") from exc


# ---------- validation ----------


def validate_request(req: ReportRequest) -> list[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors: list[str] = []
    if req.publication not in PUBLICATIONS:
        errors.append(f"Unknown publication '{req.publication}'. Valid: {sorted(PUBLICATIONS)}")
    if req.topic not in TOPICS:
        errors.append(f"Unknown topic '{req.topic}'. Valid: {sorted(TOPICS)}")
    if req.topic == "custom" and not (req.custom_topic or "").strip():
        errors.append("custom_topic is required when topic is 'custom'")
    if req.audience not in AUDIENCES:
        errors.append(f"Unknown audience '{req.audience}'. Valid: {sorted(AUDIENCES)}")
    if req.citation_mode not in CITATION_MODES:
        errors.append(f"Unknown citation_mode '{req.citation_mode}'. Valid: {sorted(CITATION_MODES)}")
    if req.confidence_mode not in CONFIDENCE_MODES:
        errors.append(f"Unknown confidence_mode '{req.confidence_mode}'. Valid: {sorted(CONFIDENCE_MODES)}")
    return errors


# ---------- evidence model ----------


def _empty_evidence() -> dict[str, Any]:
    return {
        "mode": "limited",
        "observation_count": None,
        "taxonomy_families_covered": None,
        "taxonomy_species_covered": None,
        "recent_finding_summaries": [],
        "data_sources": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_evidence_preview(
    calyx_state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Builds an evidence-preview dict from an optional calyx briefing state.
    Safe to call with no arguments — returns an empty evidence block that
    correctly declares limited mode rather than fabricating data.
    """
    evidence = _empty_evidence()

    if calyx_state is None:
        evidence["data_sources"] = []
        return evidence

    # Taxonomy coverage
    tax = calyx_state.get("taxonomy_coverage") or {}
    if tax:
        evidence["taxonomy_families_covered"] = tax.get("families_covered")
        evidence["taxonomy_species_covered"] = tax.get("species_covered")
        evidence["data_sources"].append("taxonomy_coverage")

    # Observation counts (if present in state)
    obs_count = calyx_state.get("observation_count")
    if obs_count is not None:
        evidence["observation_count"] = obs_count
        evidence["data_sources"].append("observation_engine")

    # Recent open findings that are informational (not internal-ops)
    findings = calyx_state.get("findings") or []
    summaries = [
        f["summary"]
        for f in findings
        if f.get("status") == "open" and f.get("summary")
    ][:5]
    if summaries:
        evidence["recent_finding_summaries"] = summaries
        evidence["data_sources"].append("engineering_findings")

    return evidence


# ---------- limited-evidence fallback ----------


def _topic_label(req: ReportRequest) -> str:
    if req.topic == "custom":
        return (req.custom_topic or "Custom topic").strip()
    return TOPICS.get(req.topic, req.topic)


def build_limited_evidence_summary(
    req: ReportRequest, evidence: dict[str, Any]
) -> dict[str, Any]:
    """Return a clearly labeled evidence summary when the Journalism Engine is unavailable.

    This is NOT a completed article.  It contains only operator-verified evidence
    from available Calyx data sources (taxonomy coverage, observation counts, open
    findings).  Generic prose is not fabricated; the output states explicitly that
    full article generation requires the Journalism Engine.
    """
    topic_label = _topic_label(req)
    pub_name = PUBLICATIONS.get(req.publication, req.publication)
    date_str = datetime.now(timezone.utc).strftime("%B %Y")

    lines = [
        f"# Evidence Summary: {topic_label}",
        "",
        f"*{pub_name} · {date_str}*",
        "",
        "> ⚠ **This is NOT a completed article.**",
        "> Evidence-grounded article generation requires the Journalism Engine",
        "> (`JOURNALISM_ENGINE_URL`).  This output is a structured evidence summary",
        "> only — no unsupported claims, project counts, or citations are fabricated.",
        "",
        "## Available Evidence",
        "",
    ]

    items: list[str] = []
    if evidence.get("taxonomy_families_covered") is not None:
        items.append(f"- **Orchid families tracked:** {evidence['taxonomy_families_covered']}")
    if evidence.get("taxonomy_species_covered") is not None:
        items.append(f"- **Species tracked:** {evidence['taxonomy_species_covered']}")
    if evidence.get("observation_count") is not None:
        items.append(f"- **Observations logged:** {evidence['observation_count']}")
    if evidence.get("recent_finding_summaries"):
        items.append("- **Recent open findings:**")
        for finding in evidence["recent_finding_summaries"]:
            items.append(f"  - {finding}")

    lines.extend(items if items else ["*No structured evidence available from connected data sources.*"])

    sources = evidence.get("data_sources") or []
    lines += [
        "",
        "## Data Sources",
        "",
    ]
    lines.extend([f"- {s}" for s in sources] if sources else ["*None — Calyx database not available or not connected.*"])

    if req.optional_sections:
        lines += [
            "",
            "## Requested Optional Sections (not generated — Journalism Engine required)",
            "",
        ]
        lines.extend([f"- {s}" for s in req.optional_sections])

    lines += [
        "",
        "---",
        "",
        f"*Requested topic: {topic_label}  ·  Audience: {AUDIENCES.get(req.audience, req.audience)}  ·  Target word count: {req.word_count}*",
        f"*Confidence mode: {req.confidence_mode}  ·  Citation mode: {req.citation_mode}*",
        "*To generate a full evidence-grounded article, configure `JOURNALISM_ENGINE_URL` and ensure the backend is running.*",
    ]

    markdown = "\n".join(lines)
    return {
        "markdown": markdown,
        "word_count": len(markdown.split()),
        "evidence_mode": "limited",
        "journalism_engine_available": False,
        "insufficient_evidence": not bool(sources),
        "warnings": [
            "Journalism Engine not available. Full article generation is disabled. "
            "Output is a structured evidence summary only."
        ],
        "citations": [],
        "project_table": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topic": req.topic,
        "publication": req.publication,
    }


# ---------- article assembly (retained; used by build_article()) ----------


def _mode_banner(
    req: ReportRequest, evidence: dict[str, Any], journalism_engine_available: bool = False
) -> str:
    if req.use_full_continuum and not journalism_engine_available:
        return (
            "> **⚠ Full Continuum mode requested but the Journalism Engine "
            "(`JOURNALISM_ENGINE_URL`) is not yet available.** "
            "This article was generated in limited-evidence mode instead. "
            "Project counts, current project status, and specific citations "
            "have been omitted rather than fabricated."
        )
    return (
        "> **ℹ Limited-evidence mode.** "
        "This article was generated without the Journalism Engine "
        "(`JOURNALISM_ENGINE_URL`). "
        "Specific project counts, current project status, and primary "
        "literature citations are not included. "
        "Evidence sources available: "
        + (", ".join(evidence["data_sources"]) if evidence["data_sources"] else "none")
        + "."
    )


def _header(
    req: ReportRequest, evidence: dict[str, Any], journalism_engine_available: bool = False
) -> str:
    pub_name = PUBLICATIONS.get(req.publication, req.publication)
    topic_label = _topic_label(req)
    date_str = datetime.now(timezone.utc).strftime("%B %Y")
    lines = [
        f"# {topic_label}",
        f"",
        f"*{pub_name} · {date_str}*",
        f"",
        _mode_banner(req, evidence, journalism_engine_available),
        f"",
    ]
    return "\n".join(lines)


def _conservation_survey_body(req: ReportRequest, evidence: dict[str, Any]) -> str:
    """Builds the body for the conservation_survey topic."""
    audience = AUDIENCES.get(req.audience, req.audience)
    tax_note = ""
    if evidence.get("taxonomy_families_covered"):
        tax_note = (
            f" The Orchid Continuum database currently tracks "
            f"**{evidence['taxonomy_families_covered']} orchid families**"
            + (
                f" and **{evidence['taxonomy_species_covered']} species**"
                if evidence.get("taxonomy_species_covered")
                else ""
            )
            + "."
        )

    paragraphs = [
        (
            "Orchid conservation is a global effort spanning dozens of countries, "
            "encompassing habitat protection, ex situ preservation, taxonomic research, "
            "and community engagement. For orchid enthusiasts and growers alike, "
            "understanding the scale and direction of this work helps ground our "
            "appreciation for the plants we cultivate."
        ),
        (
            "Orchids are among the most speciose plant families on Earth, "
            "occurring on every continent except Antarctica."
            + tax_note
            + " Many species face pressure from habitat loss, climate change, "
            "over-collection, and the spread of invasive species."
        ),
        (
            "Conservation programmes typically fall into three broad categories: "
            "*in situ* protection (preserving habitat where orchids naturally occur), "
            "*ex situ* collections and seed banking (safeguarding genetic diversity "
            "outside the wild), and restoration projects that reintroduce propagated "
            "plants into suitable habitats."
        ),
        (
            "Citizen science and orchid societies play a meaningful role in this work. "
            "Grower networks contribute to pollination studies, phenology monitoring, "
            "and propagation of threatened taxa. "
            "If you grow any CITES-listed species, maintaining accurate records "
            "and sourcing only from reputable nurseries directly supports conservation."
        ),
    ]
    return "\n\n".join(paragraphs)


def _generic_topic_body(req: ReportRequest, evidence: dict[str, Any]) -> str:
    topic_label = _topic_label(req)
    return (
        f"This article covers **{topic_label}** for a {AUDIENCES.get(req.audience, req.audience)} audience.\n\n"
        "Evidence available from the Orchid Continuum database has been summarised below. "
        "For a more detailed evidence-grounded article, enable full Continuum mode "
        "once the Journalism Engine (`JOURNALISM_ENGINE_URL`) is available."
    )


def _optional_sections(req: ReportRequest, evidence: dict[str, Any]) -> str:
    parts: list[str] = []
    if "calyx_perspective" in req.optional_sections:
        parts.append(
            "## Calyx Perspective\n\n"
            "The Orchid Continuum's internal intelligence layer (Calyx) monitors "
            "institutional health and research trends. "
            "At the time of this article's generation, Calyx operational state was "
            "used as a context signal only — no internal metrics are published here."
        )
    if "knowledge_gap" in req.optional_sections:
        gap_note = (
            "Key knowledge gaps identified from available evidence: "
            + (
                "; ".join(evidence["recent_finding_summaries"])
                if evidence["recent_finding_summaries"]
                else "no structured gap data available in limited-evidence mode"
            )
            + "."
        )
        parts.append(f"## Knowledge Gaps\n\n{gap_note}")
    if "grower_action" in req.optional_sections:
        parts.append(
            "## Grower Action\n\n"
            "- Source plants exclusively from reputable, documented nurseries.\n"
            "- Maintain accurate records for CITES Appendix I and II species.\n"
            "- Consider joining a regional orchid conservation working group.\n"
            "- Report unusual phenology or new naturalisations to your local society."
        )
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def _citations_section(req: ReportRequest, evidence: dict[str, Any]) -> str:
    if req.citation_mode == "none":
        return ""
    sources = evidence.get("data_sources") or []
    if not sources:
        return (
            "\n\n---\n\n"
            "*No primary literature citations are available in limited-evidence mode.*"
        )
    source_lines = [f"- Orchid Continuum internal database: {s}" for s in sources]
    header = "## Sources" if req.citation_mode in ("source_list", "numbered_endnotes") else ""
    body = "\n".join(source_lines)
    return f"\n\n---\n\n{header}\n\n{body}" if header else f"\n\n---\n\n{body}"


def _confidence_footer(req: ReportRequest) -> str:
    mode_labels = {
        "exploratory": "Exploratory — claims are directionally suggestive, not verified.",
        "standard": "Standard — claims are drawn from available institutional evidence.",
        "high_confidence_only": "High-confidence only — unverified claims have been omitted.",
    }
    label = mode_labels.get(req.confidence_mode, req.confidence_mode)
    return f"\n\n---\n\n*Confidence mode: {label}*"


def _approximate_word_count(text: str) -> int:
    return len(text.split())


def build_article(
    req: ReportRequest,
    evidence: dict[str, Any],
    journalism_engine_available: bool = False,
) -> dict[str, Any]:
    """Assemble a Markdown article from request config and evidence.
    Pure function — no DB access, fully testable.

    Pass ``journalism_engine_available=True`` to suppress the limited-mode
    banner; the API route supplies this value from the runtime capability check.
    """
    header = _header(req, evidence, journalism_engine_available)

    if req.topic == "conservation_survey":
        body = _conservation_survey_body(req, evidence)
    else:
        body = _generic_topic_body(req, evidence)

    optional = _optional_sections(req, evidence)
    citations = _citations_section(req, evidence)
    confidence = _confidence_footer(req)

    markdown = header + body + optional + citations + confidence
    word_count = _approximate_word_count(markdown)

    return {
        "markdown": markdown,
        "word_count": word_count,
        "evidence_mode": "full" if journalism_engine_available else "limited",
        "journalism_engine_available": journalism_engine_available,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topic": req.topic,
        "publication": req.publication,
    }


# ---------- API routes ----------


def _try_fetch_calyx_state() -> Optional[dict[str, Any]]:
    """Best-effort fetch of calyx state. Returns None if DB is unavailable."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None
    try:
        import calyx  # noqa: PLC0415  (local import to avoid circular dependency)

        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(database_url, row_factory=dict_row) as conn:
            return calyx.fetch_state(conn)
    except Exception:
        return None


@router.get("/evidence-preview")
def get_evidence_preview():
    """Return available evidence summary without generating an article.

    Performs a runtime capability probe against the Journalism Engine backend.
    When the backend is available the request is proxied; otherwise the local
    Calyx briefing layer is used and limited-evidence mode is declared.
    """
    capability = check_journalism_engine_capability()
    engine_available = capability.get("available", False)

    if engine_available:
        try:
            backend_data = _call_journalism_backend("POST", "/evidence-preview", body={})
            backend_data["journalism_engine_available"] = True
            backend_data.setdefault("backend_warnings", capability.get("warnings", []))
            return backend_data
        except RuntimeError:
            engine_available = False

    # Fallback: local limited-evidence
    calyx_state = _try_fetch_calyx_state()
    evidence = build_evidence_preview(calyx_state)
    return {
        "evidence": evidence,
        "journalism_engine_available": False,
        "backend_unavailable_reason": "Journalism Engine not reachable" if engine_available is False else capability.get("reason", ""),
        "backend_warnings": [],
    }


@router.post("/generate")
def generate_report(req: ReportRequest):
    """Generate a Markdown article (or evidence summary) from the provided configuration.

    When the Journalism Engine backend is reachable the request is proxied and
    the backend's Markdown, citations, project table, and warnings are returned
    directly.

    When the backend is absent or unreachable the response is a clearly labeled
    evidence summary (``build_limited_evidence_summary``); no prose is fabricated.
    """
    errors = validate_request(req)
    if errors:
        raise HTTPException(status_code=422, detail={"validation_errors": errors})

    capability = check_journalism_engine_capability()
    engine_available = capability.get("available", False)

    if engine_available:
        try:
            payload = req.model_dump()
            result = _call_journalism_backend("POST", "/generate", body=payload)
            result["journalism_engine_available"] = True
            return result
        except RuntimeError:
            engine_available = False

    # Fallback: structured evidence summary (not a completed article)
    calyx_state = _try_fetch_calyx_state()
    evidence = build_evidence_preview(calyx_state)
    result = build_limited_evidence_summary(req, evidence)
    if not capability.get("available", False):
        result["backend_unavailable_reason"] = capability.get("reason", "Journalism Engine not configured")
    else:
        result["backend_unavailable_reason"] = "Journalism Engine not reachable"
    return result
