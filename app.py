#!/usr/bin/env python3

import os
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

APP_TITLE = "Orchid Continuum API"
APP_VERSION = "1.4"


# ================================
# APP INIT
# ================================

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================================
# DATABASE
# ================================

def get_database_url() -> str:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return db_url


def get_conn() -> psycopg.Connection:
    return psycopg.connect(get_database_url(), row_factory=dict_row)


def fetch_columns(conn: psycopg.Connection, schema_name: str, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema_name, table_name),
        )
        return {row["column_name"] for row in cur.fetchall()}


def table_exists(conn: psycopg.Connection, schema_name: str, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_name = %s
            ) AS ok
            """,
            (schema_name, table_name),
        )
        row = cur.fetchone()
        return bool(row and row["ok"])


def first_existing_table(conn: psycopg.Connection, schema_name: str, candidates: list[str]) -> str | None:
    for name in candidates:
        if table_exists(conn, schema_name, name):
            return name
    return None


def first_existing_column(columns: set[str], candidates: list[str]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    return None


def build_taxonomy_name_expr(tax_cols: set[str]) -> str:
    candidates = []
    for col in ["scientific_name", "full_scientific_name", "canonical_name", "accepted_scientific_name"]:
        if col in tax_cols:
            candidates.append(f"NULLIF(t.{col}, '')")

    if not candidates:
        raise RuntimeError(
            "Could not find any usable taxonomy name column. "
            "Expected one of: scientific_name, full_scientific_name, canonical_name, accepted_scientific_name"
        )

    if len(candidates) == 1:
        return candidates[0]

    return f"COALESCE({', '.join(candidates)})"


# ================================
# BASIC ROUTES
# ================================

@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": APP_TITLE,
        "version": APP_VERSION,
        "status": "running",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/db/ping")
def db_ping() -> dict[str, Any]:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_database()::text AS database_name,
                        current_schema()::text AS schema_name,
                        current_user::text AS db_user
                    """
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("Database ping returned no row")

        return {
            "ok": True,
            "database": row["database_name"],
            "schema": row["schema_name"],
            "db_user": row["db_user"],
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database ping failed: {exc}") from exc


# ================================
# FEATURED GALLERY
# ================================

@app.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(
    limit: int = Query(default=6, ge=1, le=48),
    randomize: bool = Query(default=False),
) -> dict[str, Any]:
    """
    Working, schema-safe featured gallery.
    """
    try:
        with get_conn() as conn:
            tax_cols = fetch_columns(conn, "public", "orchid_taxonomy")
            name_expr = build_taxonomy_name_expr(tax_cols)

            order_clause = "random()" if randomize else "t.id DESC"

            sql = f"""
            SELECT
                t.id,
                {name_expr} AS scientific_name,
                i.image_url
            FROM public.orchid_images i
            JOIN public.orchid_taxonomy t
              ON i.taxonomy_id = t.id
            WHERE i.image_url IS NOT NULL
            ORDER BY {order_clause}
            LIMIT %s
            """

            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                rows = cur.fetchall()

        return {
            "widget": "featured_gallery",
            "count": len(rows),
            "cards": [
                {
                    "id": r["id"],
                    "scientific_name": r["scientific_name"],
                    "display_name": r["scientific_name"],
                    "hero_image_url": r["image_url"],
                }
                for r in rows
            ],
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Featured gallery failed: {exc}") from exc


# ================================
# REGION PROFILE
# ================================

@app.get("/api/orchid-widgets/region-profile")
def region_profile(
    value: str = Query(..., description="region slug or region name"),
) -> dict[str, Any]:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH target AS (
                        SELECT *
                        FROM oc_regions.region_profiles
                        WHERE lower(region_slug) = lower(%s)
                           OR lower(region_name) = lower(%s)
                        LIMIT 1
                    ),
                    habitats AS (
                        SELECT
                            habitat_name,
                            habitat_description,
                            image_url,
                            image_caption,
                            sort_order
                        FROM oc_regions.region_habitats
                        WHERE region_slug = (SELECT region_slug FROM target)
                        ORDER BY sort_order
                    ),
                    media AS (
                        SELECT
                            media_type,
                            media_url,
                            caption,
                            credit,
                            sort_order
                        FROM oc_regions.region_media
                        WHERE region_slug = (SELECT region_slug FROM target)
                        ORDER BY sort_order
                    )
                    SELECT
                        t.region_slug,
                        t.region_name,
                        t.scope,
                        t.parent_region_slug,
                        t.continent_name,
                        t.country_name,
                        t.display_order,
                        t.is_featured,
                        t.short_description,
                        t.orchid_significance,
                        t.habitat_summary,
                        t.climate_summary,
                        t.elevation_summary,
                        t.conservation_summary,
                        t.hero_image_url,
                        t.hero_image_caption,
                        t.video_url,
                        t.source_note,
                        COALESCE((SELECT json_agg(h) FROM habitats h), '[]'::json) AS habitats,
                        COALESCE((SELECT json_agg(m) FROM media m), '[]'::json) AS media
                    FROM target t
                    """,
                    (value, value),
                )
                row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Region not found")

        return {
            "widget": "region_profile",
            "region": row,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Region profile failed: {exc}") from exc


# ================================
# ORCHIDS BY REGION
# ================================

@app.get("/api/orchid-widgets/orchids-by-region")
def orchids_by_region(
    scope: str = Query(..., description="country | continent | island"),
    value: str = Query(..., description="Ecuador | South America | Borneo"),
    limit: int = Query(default=24, ge=1, le=200),
) -> dict[str, Any]:
    """
    Schema-aware region endpoint.
    It inspects the occurrence table/view and chooses verified columns at runtime.
    """
    try:
        scope = scope.strip().lower()
        if scope not in {"country", "continent", "island"}:
            raise HTTPException(status_code=400, detail="scope must be one of: country, continent, island")

        with get_conn() as conn:
            occurrence_table = first_existing_table(
                conn,
                "public",
                ["orchid_occurrence", "orchid_occurrences", "v_orchid_records"],
            )
            if not occurrence_table:
                raise RuntimeError(
                    "Could not find a usable occurrence source table/view. "
                    "Tried: orchid_occurrence, orchid_occurrences, v_orchid_records"
                )

            occ_cols = fetch_columns(conn, "public", occurrence_table)
            tax_cols = fetch_columns(conn, "public", "orchid_taxonomy")

            occ_taxonomy_col = first_existing_column(
                occ_cols,
                ["taxonomy_id", "taxon_id", "accepted_taxon_id"],
            )
            if not occ_taxonomy_col:
                raise RuntimeError(
                    f"Could not find a taxonomy join column in public.{occurrence_table}. "
                    "Expected one of: taxonomy_id, taxon_id, accepted_taxon_id"
                )

            scope_map = {
                "country": ["country", "country_name"],
                "continent": ["continent", "continent_name"],
                "island": ["island", "region_name", "region"],
            }
            occ_region_col = first_existing_column(occ_cols, scope_map[scope])
            if not occ_region_col:
                raise RuntimeError(
                    f"Could not find a usable {scope} column in public.{occurrence_table}. "
                    f"Tried: {', '.join(scope_map[scope])}"
                )

            name_expr = build_taxonomy_name_expr(tax_cols)

            sql = f"""
            SELECT
                t.id,
                {name_expr} AS scientific_name,
                MIN(o.{occ_region_col}) AS region_value,
                MIN(i.image_url) AS hero_image_url
            FROM public.{occurrence_table} o
            JOIN public.orchid_taxonomy t
              ON o.{occ_taxonomy_col} = t.id
            LEFT JOIN public.orchid_images i
              ON i.taxonomy_id = t.id
            WHERE lower(COALESCE(o.{occ_region_col}, '')) = lower(%s)
              AND i.image_url IS NOT NULL
            GROUP BY
                t.id,
                {name_expr}
            ORDER BY t.id DESC
            LIMIT %s
            """

            with conn.cursor() as cur:
                cur.execute(sql, (value, limit))
                rows = cur.fetchall()

        return {
            "widget": "orchids_by_region",
            "scope": scope,
            "value": value,
            "count": len(rows),
            "orchids": [
                {
                    "id": r["id"],
                    "scientific_name": r["scientific_name"],
                    "display_name": r["scientific_name"],
                    "region_value": r["region_value"],
                    "hero_image_url": r["hero_image_url"],
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Orchids by region failed: {exc}") from exc
