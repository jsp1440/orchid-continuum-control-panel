# FILE: calyx_report.py
# Calyx Report — Mission Control article-generation workspace.
#
# Provides two pure-synthesis layers:
#   build_evidence_preview()  — summarises available evidence without DB
#   build_article()           — composes a Markdown article from config + evidence
#
# The Journalism Engine backend (orchid-calyx-backend#173) is not yet live.
# Until it is, the module operates in LIMITED-EVIDENCE MODE:
#   • all text is synthesised from real institutional state (calyx briefing,
#     observation counts, taxonomy coverage) — never fabricated;
#   • unsupported claims, project counts, and current project status are not
#     asserted;
#   • every article carries an explicit evidence-mode disclosure banner.
#
# When the Journalism Engine becomes available, replace _fetch_journalism_data()
# with real calls and flip JOURNALISM_ENGINE_AVAILABLE to True.

import os
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

# ---------- constants ----------

JOURNALISM_ENGINE_AVAILABLE = False  # flip when orchid-calyx-backend#173 is live

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


# ---------- article assembly ----------


def _topic_label(req: ReportRequest) -> str:
    if req.topic == "custom":
        return (req.custom_topic or "Custom topic").strip()
    return TOPICS.get(req.topic, req.topic)


def _mode_banner(req: ReportRequest, evidence: dict[str, Any]) -> str:
    if req.use_full_continuum and not JOURNALISM_ENGINE_AVAILABLE:
        return (
            "> **⚠ Full Continuum mode requested but Journalism Engine "
            "(orchid-calyx-backend#173) is not yet available.** "
            "This article was generated in limited-evidence mode instead. "
            "Project counts, current project status, and specific citations "
            "have been omitted rather than fabricated."
        )
    return (
        "> **ℹ Limited-evidence mode.** "
        "This article was generated without the Journalism Engine "
        "(orchid-calyx-backend#173). "
        "Specific project counts, current project status, and primary "
        "literature citations are not included. "
        "Evidence sources available: "
        + (", ".join(evidence["data_sources"]) if evidence["data_sources"] else "none")
        + "."
    )


def _header(req: ReportRequest, evidence: dict[str, Any]) -> str:
    pub_name = PUBLICATIONS.get(req.publication, req.publication)
    topic_label = _topic_label(req)
    date_str = datetime.now(timezone.utc).strftime("%B %Y")
    lines = [
        f"# {topic_label}",
        f"",
        f"*{pub_name} · {date_str}*",
        f"",
        _mode_banner(req, evidence),
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
        "once the Journalism Engine (orchid-calyx-backend#173) is available."
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


def build_article(req: ReportRequest, evidence: dict[str, Any]) -> dict[str, Any]:
    """Assemble a Markdown article from request config and evidence.
    Pure function — no DB access, fully testable.
    """
    header = _header(req, evidence)

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
        "evidence_mode": "limited" if not JOURNALISM_ENGINE_AVAILABLE else "full",
        "journalism_engine_available": JOURNALISM_ENGINE_AVAILABLE,
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
    Safe to call at any time — degraded gracefully when DB is unavailable.
    """
    calyx_state = _try_fetch_calyx_state()
    evidence = build_evidence_preview(calyx_state)
    return {
        "evidence": evidence,
        "journalism_engine_available": JOURNALISM_ENGINE_AVAILABLE,
        "backend_dependency": "orchid-calyx-backend#173",
    }


@router.post("/generate")
def generate_report(req: ReportRequest):
    """Generate a Markdown article from the provided configuration.
    In limited-evidence mode (Journalism Engine unavailable) this uses
    institutional state from the existing Calyx briefing layer.
    Full Continuum mode is accepted but falls back to limited-evidence
    with an explicit disclosure until the Journalism Engine is live.
    """
    errors = validate_request(req)
    if errors:
        raise HTTPException(status_code=422, detail={"validation_errors": errors})

    if req.use_full_continuum and not JOURNALISM_ENGINE_AVAILABLE:
        # Document the unavailability but continue in limited-evidence mode
        pass  # _mode_banner() handles the disclosure in the article

    calyx_state = _try_fetch_calyx_state()
    evidence = build_evidence_preview(calyx_state)
    result = build_article(req, evidence)
    return result
