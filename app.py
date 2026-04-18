#!/usr/bin/env python3

import os
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

APP_TITLE = "Orchid Continuum API"
APP_VERSION = "1.2"


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


def get_database_url() -> str:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return db_url


def get_conn() -> psycopg.Connection:
    return psycopg.connect(get_database_url(), row_factory=dict_row)


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


@app.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(
    limit: int = Query(default=6, ge=1, le=48),
    randomize: bool = Query(default=False),
) -> dict[str, Any]:
    """
    Minimal schema-safe version using only verified fields:
    orchid_images:
      - id
      - taxonomy_id
      - image_url
      - image_source
      - image_type
    orchid_taxonomy:
      - id
      - scientific_name
      - full_scientific_name
      - genus
      - family_name
    """
    try:
        order_clause = "random()" if randomize else "t.id DESC, i.id DESC"

        sql = f"""
        SELECT
            t.id,
            COALESCE(
                NULLIF(t.scientific_name, ''),
                NULLIF(t.full_scientific_name, '')
            ) AS scientific_name,
            t.genus,
            t.family_name AS family,
            i.image_url,
            i.image_source,
            i.image_type
        FROM orchid_images i
        JOIN orchid_taxonomy t
          ON i.taxonomy_id = t.id
        WHERE i.image_url IS NOT NULL
        ORDER BY {order_clause}
        LIMIT %s
        """

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                rows = cur.fetchall()

        cards = [
            {
                "id": row["id"],
                "scientific_name": row["scientific_name"],
                "display_name": row["scientific_name"],
                "genus": row["genus"],
                "family": row["family"],
                "hero_image_url": row["image_url"],
                "image_source": row["image_source"],
                "image_type": row["image_type"],
            }
            for row in rows
        ]

        return {
            "widget": "featured_gallery",
            "count": len(cards),
            "cards": cards,
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Featured gallery failed: {exc}") from exc


@app.get("/api/orchid-widgets/region-profile")
def region_profile(
    value: str = Query(..., description="region slug or region name"),
) -> dict[str, Any]:
    try:
        sql = """
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
        """

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (value, value))
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
