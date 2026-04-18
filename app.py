#!/usr/bin/env python3

import os
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
import psycopg

APP_TITLE = "Orchid Continuum API"
APP_VERSION = "1.6"

app = FastAPI(title=APP_TITLE, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def fetch_columns(conn, schema_name: str, table_name: str) -> set[str]:
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


def build_taxonomy_name_expr(tax_cols: set[str]) -> str:
    candidates = []
    for col in ["scientific_name", "full_scientific_name", "canonical_name", "accepted_scientific_name"]:
        if col in tax_cols:
            candidates.append(f"NULLIF(t.{col}, '')")

    if not candidates:
        raise RuntimeError(
            "Could not find a usable taxonomy name column in orchid_taxonomy"
        )

    if len(candidates) == 1:
        return candidates[0]

    return f"COALESCE({', '.join(candidates)})"


@app.get("/")
def root():
    return {
        "service": APP_TITLE,
        "version": APP_VERSION,
        "status": "running",
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/db/ping")
def db_ping():
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

        return {
            "ok": True,
            "database": row["database_name"],
            "schema": row["schema_name"],
            "db_user": row["db_user"],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database ping failed: {exc}")


@app.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(
    limit: int = Query(default=6, ge=1, le=48),
    randomize: bool = Query(default=False),
):
    try:
        with get_conn() as conn:
            tax_cols = fetch_columns(conn, "public", "orchid_taxonomy")
            name_expr = build_taxonomy_name_expr(tax_cols)
            order_clause = "random()" if randomize else "t.id DESC"

            sql = f"""
            SELECT
                t.id,
                {name_expr} AS scientific_name,
                MIN(i.image_url) AS hero_image_url
            FROM public.orchid_images i
            JOIN public.orchid_taxonomy t
              ON i.taxonomy_id = t.id
            WHERE i.image_url IS NOT NULL
            GROUP BY t.id, {name_expr}
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
                    "hero_image_url": r["hero_image_url"],
                }
                for r in rows
            ],
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Featured gallery failed: {exc}")


@app.get("/api/orchid-widgets/region-profile")
def region_profile(
    value: str = Query(..., description="region slug, alias, or region name"),
):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH target AS (
                        SELECT rp.*
                        FROM oc_regions.region_profiles rp
                        WHERE lower(rp.region_slug) = lower(%s)
                           OR lower(rp.region_name) = lower(%s)

                        UNION ALL

                        SELECT rp.*
                        FROM oc_regions.region_aliases ra
                        JOIN oc_regions.region_profiles rp
                          ON rp.region_slug = ra.region_slug
                        WHERE lower(ra.alias) = lower(%s)

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
                    (value, value, value),
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
        raise HTTPException(status_code=500, detail=f"Region profile failed: {exc}")


@app.get("/api/orchid-widgets/orchids-by-region")
def orchids_by_region(
    scope: str = Query(..., description="country | region | island | continent"),
    value: str = Query(..., description="Ecuador | Borneo | South America"),
    limit: int = Query(default=24, ge=1, le=200),
):
    try:
        normalized_scope = scope.strip().lower()

        if normalized_scope not in {"country", "region", "island", "continent"}:
            raise HTTPException(
                status_code=400,
                detail="scope must be one of: country, region, island, continent",
            )

        with get_conn() as conn:
            tax_cols = fetch_columns(conn, "public", "orchid_taxonomy")
            occ_cols = fetch_columns(conn, "public", "orchid_occurrence")
            name_expr = build_taxonomy_name_expr(tax_cols)

            required_occ = {"taxonomy_id", "country", "region", "scientific_name"}
            missing = sorted(required_occ - occ_cols)
            if missing:
                raise RuntimeError(f"public.orchid_occurrence is missing required columns: {', '.join(missing)}")

            if normalized_scope == "country":
                sql = """
                SELECT
                    o.taxonomy_id AS id,
                    COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid') AS scientific_name,
                    MIN(o.country) AS matched_value,
                    MIN(i.image_url) AS hero_image_url
                FROM public.orchid_occurrence o
                LEFT JOIN public.orchid_images i
                  ON i.taxonomy_id = o.taxonomy_id
                WHERE lower(COALESCE(o.country, '')) = lower(%s)
                  AND i.image_url IS NOT NULL
                GROUP BY o.taxonomy_id, COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid')
                ORDER BY o.taxonomy_id DESC
                LIMIT %s
                """
                params = (value, limit)
                match_strategy = "direct_country"

            elif normalized_scope in {"region", "island"}:
                sql = """
                SELECT
                    o.taxonomy_id AS id,
                    COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid') AS scientific_name,
                    MIN(COALESCE(o.region, o.country)) AS matched_value,
                    MIN(i.image_url) AS hero_image_url
                FROM public.orchid_occurrence o
                LEFT JOIN public.orchid_images i
                  ON i.taxonomy_id = o.taxonomy_id
                WHERE (
                        lower(COALESCE(o.region, '')) = lower(%s)
                     OR lower(COALESCE(o.country, '')) = lower(%s)
                     OR lower(COALESCE(o.country, '')) IN (
                        SELECT lower(country_name)
                        FROM oc_regions.region_country_members
                        WHERE lower(region_slug) = lower(%s)
                     )
                )
                  AND i.image_url IS NOT NULL
                GROUP BY o.taxonomy_id, COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid')
                ORDER BY o.taxonomy_id DESC
                LIMIT %s
                """
                params = (value, value, value, limit)
                match_strategy = "direct_region_country_or_curated_membership"

            else:  # continent
                sql = """
                SELECT
                    o.taxonomy_id AS id,
                    COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid') AS scientific_name,
                    MIN(o.country) AS matched_value,
                    MIN(i.image_url) AS hero_image_url
                FROM public.orchid_occurrence o
                LEFT JOIN public.orchid_images i
                  ON i.taxonomy_id = o.taxonomy_id
                WHERE lower(COALESCE(o.country, '')) IN (
                    SELECT lower(rcm.country_name)
                    FROM oc_regions.region_country_members rcm
                    WHERE lower(rcm.region_slug) = lower(%s)
                )
                  AND i.image_url IS NOT NULL
                GROUP BY o.taxonomy_id, COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid')
                ORDER BY o.taxonomy_id DESC
                LIMIT %s
                """
                continent_slug = value.strip().lower().replace(" ", "-")
                params = (continent_slug, limit)
                match_strategy = "continent_via_region_country_members"

            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        orchids = [
            {
                "id": r["id"],
                "scientific_name": r["scientific_name"],
                "display_name": r["scientific_name"],
                "matched_value": r["matched_value"],
                "hero_image_url": r["hero_image_url"],
            }
            for r in rows
        ]

        response = {
            "widget": "orchids_by_region",
            "scope": normalized_scope,
            "value": value,
            "count": len(orchids),
            "match_strategy": match_strategy,
            "orchids": orchids,
        }

        if normalized_scope in {"region", "island"} and len(orchids) == 0:
            response["mapping_note"] = (
                "No direct or curated matches were found for this region/island in orchid_occurrence."
            )

        if normalized_scope == "continent" and len(orchids) == 0:
            response["mapping_note"] = (
                "No orchid_occurrence rows matched the curated country membership for this continent."
            )

        return response

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Orchids by region failed: {exc}")
