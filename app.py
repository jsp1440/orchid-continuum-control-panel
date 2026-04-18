#!/usr/bin/env python3

import os
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

APP_TITLE = "Orchid Continuum API"
APP_VERSION = "1.3"


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

        return {
            "ok": True,
            "database": row["database_name"],
            "schema": row["schema_name"],
            "db_user": row["db_user"],
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database ping failed: {exc}")


# ================================
# FEATURED GALLERY (WORKING)
# ================================

@app.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(
    limit: int = Query(default=6, ge=1, le=48),
    randomize: bool = Query(default=False),
) -> dict[str, Any]:

    try:
        order_clause = "random()" if randomize else "t.id DESC"

        sql = f"""
        SELECT
            t.id,
            COALESCE(
                NULLIF(t.scientific_name, ''),
                NULLIF(t.full_scientific_name, '')
            ) AS scientific_name,
            i.image_url
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
        raise HTTPException(status_code=500, detail=f"Featured gallery failed: {exc}")


# ================================
# ORCHIDS BY REGION (NEW)
# ================================

@app.get("/api/orchid-widgets/orchids-by-region")
def orchids_by_region(
    region: str = Query(...),
    limit: int = Query(default=24, ge=1, le=200),
) -> dict[str, Any]:

    try:
        sql = """
        SELECT DISTINCT
            t.id,
            t.scientific_name,
            i.image_url,
            o.country
        FROM orchid_occurrence o
        JOIN orchid_taxonomy t ON o.taxonomy_id = t.id
        LEFT JOIN orchid_images i ON i.taxonomy_id = t.id
        WHERE lower(o.country) = lower(%s)
        AND i.image_url IS NOT NULL
        LIMIT %s
        """

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (region, limit))
                rows = cur.fetchall()

        return {
            "widget": "orchids_by_region",
            "region": region,
            "count": len(rows),
            "orchids": rows,
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Region query failed: {exc}")


# ================================
# REGION PROFILE (UNCHANGED)
# ================================

@app.get("/api/orchid-widgets/region-profile")
def region_profile(value: str = Query(...)):

    try:
        sql = """
        SELECT *
        FROM oc_regions.region_profiles
        WHERE lower(region_name) = lower(%s)
        LIMIT 1
        """

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (value,))
                row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Region not found")

        return {
            "widget": "region_profile",
            "region": row,
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Region profile failed: {exc}")
