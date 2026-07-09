# FILE: operational.py
# Mission Control operational inventory.
#
# This module is intentionally descriptive and evidence-backed. It does not
# perform health checks that the repository cannot actually perform, and it
# reports missing pipelines as not-yet-implemented rather than inventing data.

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from admin import require_admin_token

router = APIRouter(
    prefix="/api/v1/mission-control",
    tags=["Mission Control"],
    dependencies=[Depends(require_admin_token)],
)

DATABASE_URL = os.getenv("DATABASE_URL")
BASE_DIR = Path(__file__).resolve().parent

OPERATIONAL = "operational"
PARTIAL = "partially_implemented"
PLACEHOLDER = "placeholder"
DISCONNECTED = "disconnected"
MISSING_DEPENDENCY = "missing_dependency"
PIPELINE_NOT_IMPLEMENTED = "pipeline_not_yet_implemented"


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def _table_exists(conn, schema_name: str, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
            ) AS exists
            """,
            (schema_name, table_name),
        )
        return bool(cur.fetchone()["exists"])


def _count(conn, schema_name: str, table_name: str) -> int | None:
    if not _table_exists(conn, schema_name, table_name):
        return None
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*)::int AS n FROM {schema_name}.{table_name}")
        return int(cur.fetchone()["n"])


def _file_exists(path: str) -> bool:
    return (BASE_DIR / path).exists()


def _route(path: str, method: str = "GET") -> str:
    return f"{method} {path}"


MISSION_CONTROL_MODULES: list[dict[str, Any]] = [
    {
        "key": "engineering_memory",
        "name": "Engineering Memory",
        "status": OPERATIONAL,
        "evidence": ["memory.py", "engineering-memory.html", _route("/api/v1/memory/decisions")],
        "tables": ["oc_memory_decisions", "oc_memory_decision_relationships", "oc_memory_decision_links"],
        "next_action": "Use the existing decision lifecycle and link records to keep implementation evidence current.",
    },
    {
        "key": "brain_outbox",
        "name": "Brain Outbox",
        "status": PARTIAL,
        "evidence": ["memory.py", _route("/api/v1/memory/outbox"), "BRAIN_SYNC_ENDPOINT"],
        "tables": ["oc_memory_outbox", "oc_memory_outbox_events"],
        "next_action": "Implement a real Brain adapter that drains pending outbox rows when BRAIN_SYNC_ENDPOINT is available.",
    },
    {
        "key": "observation_engine",
        "name": "Observation Engine",
        "status": OPERATIONAL,
        "evidence": ["observation.py", "observations.html", _route("/api/v1/observations/summary")],
        "tables": ["oc_observations", "oc_observation_events"],
        "next_action": "Run through the existing Agent Queue and preserve observation history for Evaluation.",
    },
    {
        "key": "evaluation_engine",
        "name": "Evaluation Engine",
        "status": OPERATIONAL,
        "evidence": ["evaluation.py", _route("/api/v1/calyx/evaluate", "POST"), "test_evaluation.py"],
        "tables": [],
        "next_action": "Continue using deterministic scoring until new real data sources exist.",
    },
    {
        "key": "mission_brief",
        "name": "Mission Brief",
        "status": OPERATIONAL,
        "evidence": ["calyx.py", "calyx.html", _route("/api/v1/calyx/mission-brief")],
        "tables": [],
        "next_action": "Keep it read-only except for explicit /evaluate proposal creation.",
    },
    {
        "key": "agent_registry",
        "name": "Agent Registry",
        "status": OPERATIONAL,
        "evidence": ["agents.py", "agents.html", _route("/api/v1/agents")],
        "tables": ["oc_agent_registry"],
        "next_action": "Register future agents here rather than creating parallel execution paths.",
    },
    {
        "key": "agent_queue",
        "name": "Agent Queue",
        "status": OPERATIONAL,
        "evidence": ["agents.py", _route("/api/v1/agents/{agent_key}/run", "POST")],
        "tables": ["oc_agent_tasks", "oc_agent_task_events"],
        "next_action": "Add a scheduler only after a real execution policy is approved.",
    },
    {
        "key": "findings",
        "name": "Findings",
        "status": OPERATIONAL,
        "evidence": ["agents.py", _route("/api/v1/agents/{agent_key}/findings")],
        "tables": ["oc_agent_findings"],
        "next_action": "Keep findings reviewable; do not promote agent output directly to decisions.",
    },
    {
        "key": "health_dashboard",
        "name": "Health Dashboard",
        "status": PARTIAL,
        "evidence": [_route("/health"), _route("/db/ping"), _route("/api/brain/status")],
        "tables": [],
        "next_action": "Replace raw endpoints with a richer dashboard only after the operational status API is consumed.",
    },
    {
        "key": "repository_status",
        "name": "Repository Status",
        "status": PIPELINE_NOT_IMPLEMENTED,
        "evidence": ["admin.html disabled card", "README.md planned module"],
        "tables": [],
        "next_action": "Add a GitHub-backed repository status integration; no local table or API exists today.",
    },
    {
        "key": "deployment_status",
        "name": "Deployment Status",
        "status": PARTIAL,
        "evidence": ["render.yaml", "RENDER_GIT_COMMIT observed by observation.py"],
        "tables": [],
        "next_action": "Record deployment events and compare deployed commit to repository HEAD once GitHub status exists.",
    },
    {
        "key": "scheduled_jobs",
        "name": "Scheduled Jobs",
        "status": PIPELINE_NOT_IMPLEMENTED,
        "evidence": ["observation.py scheduled_scan_stub", "README.md says agent runs are manual"],
        "tables": [],
        "next_action": "Document desired frequencies before adding automation.",
    },
]


SCIENCE_PIPELINES: list[dict[str, Any]] = [
    {
        "key": "taxonomy",
        "name": "Taxonomy",
        "status": PARTIAL,
        "data_source": "public.orchid_taxonomy",
        "api_endpoint": "/api/species/search, /api/species/metrics, /api/genus/{genus}",
        "database_dependency": "public.orchid_taxonomy",
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["No scheduler or ownership runner in this repository."],
        "recommended_next_action": "Add table-aware health reporting and connect taxonomy completeness to Observation Engine.",
        "confidence": "high",
    },
    {
        "key": "images",
        "name": "Images",
        "status": PARTIAL,
        "data_source": "public.images, public.orchid_images",
        "api_endpoint": "/images/genus/{genus}, /api/orchid-widgets/featured-gallery",
        "database_dependency": "public.images, public.orchid_images",
        "background_runner": "oc_harvester_shim.py",
        "scheduler": None,
        "known_blockers": ["Harvester shim exists, but no scheduler is configured in render.yaml."],
        "recommended_next_action": "Expose harvester heartbeat/run state in Mission Control.",
        "confidence": "high",
    },
    {
        "key": "atlas",
        "name": "Atlas",
        "status": PARTIAL,
        "data_source": "oc_regions.*, oc_intelligence.v_region_species_summary, public.images fallback",
        "api_endpoint": "/atlas.html, /api/orchid-widgets/region-profile, /api/orchid-widgets/region-intelligence",
        "database_dependency": "oc_regions.*, oc_intelligence.*, public.images",
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["Region enrichment tables are optional and may be absent."],
        "recommended_next_action": "Report Atlas table availability through Mission Control.",
        "confidence": "high",
    },
    {
        "key": "occurrences",
        "name": "Occurrences",
        "status": PARTIAL,
        "data_source": "public.orchid_occurrence",
        "api_endpoint": "/api/species/metrics",
        "database_dependency": "public.orchid_occurrence",
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["Only count-level consumption is present in this repository."],
        "recommended_next_action": "Add occurrence-specific health and freshness observations.",
        "confidence": "medium",
    },
    {
        "key": "knowledge_graph",
        "name": "Knowledge Graph",
        "status": PIPELINE_NOT_IMPLEMENTED,
        "data_source": None,
        "api_endpoint": None,
        "database_dependency": None,
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["No graph tables, endpoints, or runner exist in this repository."],
        "recommended_next_action": "Define source tables before adding a graph API.",
        "confidence": "high",
    },
    {
        "key": "literature",
        "name": "Literature",
        "status": PIPELINE_NOT_IMPLEMENTED,
        "data_source": None,
        "api_endpoint": None,
        "database_dependency": None,
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["evaluation.py and observation.py both document no literature data source."],
        "recommended_next_action": "Create a literature ingestion design and table contract.",
        "confidence": "high",
    },
    {
        "key": "pollinators",
        "name": "Pollinators",
        "status": PIPELINE_NOT_IMPLEMENTED,
        "data_source": None,
        "api_endpoint": None,
        "database_dependency": None,
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["No pollinator relationship source exists in this repository."],
        "recommended_next_action": "Define pollinator relationship schema and ingestion source.",
        "confidence": "high",
    },
    {
        "key": "mycorrhiza",
        "name": "Mycorrhiza",
        "status": PIPELINE_NOT_IMPLEMENTED,
        "data_source": None,
        "api_endpoint": None,
        "database_dependency": None,
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["No mycorrhizal relationship source exists in this repository."],
        "recommended_next_action": "Define mycorrhizal relationship schema and ingestion source.",
        "confidence": "high",
    },
    {
        "key": "climate",
        "name": "Climate",
        "status": PIPELINE_NOT_IMPLEMENTED,
        "data_source": None,
        "api_endpoint": None,
        "database_dependency": None,
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["Region profile returns pending climate enrichment text; no climate table is present in code."],
        "recommended_next_action": "Connect a real climate source before surfacing climate health.",
        "confidence": "medium",
    },
    {
        "key": "habitat",
        "name": "Habitat",
        "status": PARTIAL,
        "data_source": "oc_regions.region_habitats when present",
        "api_endpoint": "/api/orchid-widgets/region-profile",
        "database_dependency": "oc_regions.region_habitats",
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["Falls back to generic pending text when curated tables are missing."],
        "recommended_next_action": "Add explicit habitat table availability to the operational dashboard.",
        "confidence": "medium",
    },
    {
        "key": "conservation",
        "name": "Conservation",
        "status": PIPELINE_NOT_IMPLEMENTED,
        "data_source": None,
        "api_endpoint": None,
        "database_dependency": None,
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["Species dossier reports pending conservation layer connection."],
        "recommended_next_action": "Create conservation layer table/API contract.",
        "confidence": "high",
    },
    {
        "key": "education",
        "name": "Education",
        "status": PIPELINE_NOT_IMPLEMENTED,
        "data_source": None,
        "api_endpoint": None,
        "database_dependency": None,
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["Mission Control landing page has Education / OCU as a disabled card."],
        "recommended_next_action": "Define Education/OCU operational records before building UI.",
        "confidence": "high",
    },
    {
        "key": "species",
        "name": "Species",
        "status": PARTIAL,
        "data_source": "public.orchid_taxonomy plus images",
        "api_endpoint": "/api/species/*, /api/species-dossier/{canonical_name}",
        "database_dependency": "public.orchid_taxonomy, public.images",
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["Dossier sections for traits, relationships, literature, video, and conservation are pending."],
        "recommended_next_action": "Connect missing dossier sections only after their real tables exist.",
        "confidence": "high",
    },
    {
        "key": "homepage",
        "name": "Homepage",
        "status": PARTIAL,
        "data_source": "Mission Control public API endpoints in app.py",
        "api_endpoint": "/api/genus/daily, /api/species/featured, /api/species/metrics, /api/orchid-widgets/*",
        "database_dependency": "public.orchid_taxonomy, public.images, public.orchid_occurrence",
        "background_runner": None,
        "scheduler": None,
        "known_blockers": ["The public website is a separate repository and is not integrated here."],
        "recommended_next_action": "Have the public website consume these APIs rather than duplicating pipeline logic.",
        "confidence": "high",
    },
]


HOMEPAGE_INTEGRATION: list[dict[str, Any]] = [
    {"surface": "Featured Genus", "current_source": "/api/genus/daily", "desired_source": "Mission Control genus packet", "missing": ["No freshness/scheduler signal."], "status": PARTIAL},
    {"surface": "Featured Species", "current_source": "/api/species/featured", "desired_source": "Mission Control species pipeline", "missing": ["No editorial selection table."], "status": PARTIAL},
    {"surface": "Hero Image", "current_source": "public.images/public.orchid_images via genus endpoints", "desired_source": "Mission Control image pipeline", "missing": ["No curated hero-image policy table."], "status": PARTIAL},
    {"surface": "Image Rotation", "current_source": "/api/orchid-widgets/featured-gallery", "desired_source": "Mission Control image pipeline", "missing": ["No scheduler or rotation history."], "status": PARTIAL},
    {"surface": "Science Cards", "current_source": "/api/genus-story/{genus} shell", "desired_source": "Mission Control science pipeline inventory", "missing": ["Literature, pollinator, mycorrhiza, conservation pipelines."], "status": PARTIAL},
    {"surface": "Knowledge Graph", "current_source": None, "desired_source": "Mission Control knowledge graph pipeline", "missing": ["API", "tables", "runner"], "status": PIPELINE_NOT_IMPLEMENTED},
    {"surface": "Habitat Cards", "current_source": "oc_regions.region_habitats when present", "desired_source": "Mission Control habitat pipeline", "missing": ["Availability and freshness observations."], "status": PARTIAL},
    {"surface": "Atlas", "current_source": "/api/orchid-widgets/region-*", "desired_source": "Mission Control Atlas pipeline", "missing": ["Operational status rollup."], "status": PARTIAL},
    {"surface": "Statistics", "current_source": "/api/species/metrics", "desired_source": "Mission Control metrics pipeline", "missing": ["Gaps for genera/countries/last_updated remain null."], "status": PARTIAL},
    {"surface": "Pollinators", "current_source": None, "desired_source": "Mission Control pollinator pipeline", "missing": ["API", "tables", "runner"], "status": PIPELINE_NOT_IMPLEMENTED},
    {"surface": "Mycorrhiza", "current_source": None, "desired_source": "Mission Control mycorrhiza pipeline", "missing": ["API", "tables", "runner"], "status": PIPELINE_NOT_IMPLEMENTED},
    {"surface": "Literature", "current_source": None, "desired_source": "Mission Control literature pipeline", "missing": ["API", "tables", "runner"], "status": PIPELINE_NOT_IMPLEMENTED},
    {"surface": "Education", "current_source": None, "desired_source": "Mission Control Education/OCU pipeline", "missing": ["API", "tables", "operational model"], "status": PIPELINE_NOT_IMPLEMENTED},
]


SCHEDULE_RECOMMENDATIONS = [
    {"cadence": "continuous", "tasks": ["API liveness", "database connectivity"], "implementation_status": PARTIAL},
    {"cadence": "hourly", "tasks": ["Observation Engine scan", "Agent Queue failure review"], "implementation_status": PIPELINE_NOT_IMPLEMENTED},
    {"cadence": "daily", "tasks": ["Calyx Mission Brief review", "Brain Outbox retry"], "implementation_status": PIPELINE_NOT_IMPLEMENTED},
    {"cadence": "weekly", "tasks": ["Science pipeline coverage audit", "Repository/deployment review"], "implementation_status": PIPELINE_NOT_IMPLEMENTED},
    {"cadence": "monthly", "tasks": ["Operational readiness report", "Education/conservation partner review"], "implementation_status": PIPELINE_NOT_IMPLEMENTED},
]


def annotate_module_tables(modules: list[dict[str, Any]], table_counts: dict[str, int | None]) -> list[dict[str, Any]]:
    annotated = []
    for module in modules:
        item = dict(module)
        item["files_present"] = [e for e in item["evidence"] if e.endswith((".py", ".html", ".md")) and _file_exists(e)]
        item["table_counts"] = {table: table_counts.get(table) for table in item.get("tables", [])}
        annotated.append(item)
    return annotated


def summarize_status(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = item["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def readiness_score(modules: list[dict[str, Any]], pipelines: list[dict[str, Any]]) -> dict[str, Any]:
    weights = {OPERATIONAL: 1.0, PARTIAL: 0.5, PLACEHOLDER: 0.15, DISCONNECTED: 0.0, MISSING_DEPENDENCY: 0.0, PIPELINE_NOT_IMPLEMENTED: 0.0}
    items = modules + pipelines
    score = round(100 * sum(weights.get(item["status"], 0.0) for item in items) / len(items))
    return {
        "score": score,
        "scale": "0-100",
        "method": "Operational=1.0, partially_implemented=0.5, placeholder=0.15, disconnected/missing/pipeline_not_yet_implemented=0.0 across modules and science pipelines.",
    }


def build_operational_status(table_counts: dict[str, int | None] | None = None, db_reachable: bool = False, db_error: str | None = None) -> dict[str, Any]:
    table_counts = table_counts or {}
    modules = annotate_module_tables(MISSION_CONTROL_MODULES, table_counts)
    pipelines = [dict(pipeline) for pipeline in SCIENCE_PIPELINES]
    status_counts = {
        "mission_control": summarize_status(modules),
        "science_pipelines": summarize_status(pipelines),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": {
            "reachable": db_reachable,
            "error": db_error,
            "table_counts": table_counts,
        },
        "mission_control_modules": modules,
        "science_pipelines": pipelines,
        "homepage_integration": HOMEPAGE_INTEGRATION,
        "schedule_recommendations": SCHEDULE_RECOMMENDATIONS,
        "status_counts": status_counts,
        "readiness": readiness_score(modules, pipelines),
        "deployment_required": {
            "frontend": False,
            "backend": True,
            "database_migration": False,
            "render_config": False,
            "evidence": "This change adds a backend API and admin HTML only; tables are still lazily created with CREATE TABLE IF NOT EXISTS.",
        },
        "next_five_builds": [
            "Expose harvester heartbeat and run state in Mission Control.",
            "Add repository/deployment status from a real GitHub/Render integration.",
            "Implement Brain Outbox drain/retry adapter.",
            "Connect Observation Engine history into Evaluation Engine scoring.",
            "Define first real literature or pollinator pipeline table/API contract.",
        ],
        "confidence": "high for repository-local inventory; medium for database table availability when DATABASE_URL is absent or unreachable.",
    }


@router.get("/status")
def get_operational_status():
    db_reachable = False
    db_error = None
    table_counts: dict[str, int | None] = {}
    tables = {
        "oc_memory_decisions": ("public", "oc_memory_decisions"),
        "oc_memory_outbox": ("public", "oc_memory_outbox"),
        "oc_agent_registry": ("public", "oc_agent_registry"),
        "oc_agent_tasks": ("public", "oc_agent_tasks"),
        "oc_agent_findings": ("public", "oc_agent_findings"),
        "oc_observations": ("public", "oc_observations"),
        "orchid_taxonomy": ("public", "orchid_taxonomy"),
        "images": ("public", "images"),
        "orchid_images": ("public", "orchid_images"),
        "orchid_occurrence": ("public", "orchid_occurrence"),
    }

    try:
        with get_conn() as conn:
            db_reachable = True
            for key, (schema_name, table_name) in tables.items():
                table_counts[key] = _count(conn, schema_name, table_name)
    except Exception as exc:
        db_error = str(exc)

    try:
        return build_operational_status(table_counts=table_counts, db_reachable=db_reachable, db_error=db_error)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Operational status failed: {exc}")
