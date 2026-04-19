#!/usr/bin/env python3

import os
from pathlib import Path

import psycopg
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from psycopg.rows import dict_row

app = FastAPI(title="Orchid Continuum API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def find_atlas_html():
    base = Path(__file__).parent
    for p in ["atlas.html", "static/atlas.html"]:
        f = base / p
        if f.exists():
            return f
    raise HTTPException(404, "atlas.html not found")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/atlas.html")
def atlas():
    return FileResponse(find_atlas_html(), media_type="text/html")


# 🔧 FIXED REGION PROFILE (adds habitat image fallback)
@app.get("/api/orchid-widgets/region-profile")
def region_profile(value: str):

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:

                cur.execute("""
                SELECT *
                FROM oc_regions.region_profiles
                WHERE lower(region_name) = lower(%s)
                LIMIT 1
                """, (value,))
                region = cur.fetchone()

                if not region:
                    raise HTTPException(404, "Region not found")

                # 🔥 KEY FIX: fallback image logic
                cur.execute("""
                SELECT
                    habitat_name,
                    habitat_description,
                    COALESCE(
                        image_url,
                        (SELECT MIN(i.image_url)
                         FROM orchid_occurrence o
                         JOIN orchid_images i
                           ON i.taxonomy_id = o.taxonomy_id
                         WHERE lower(o.country) = lower(%s)
                         AND i.image_url IS NOT NULL)
                    ) AS image_url
                FROM oc_regions.region_habitats
                WHERE region_slug = %s
                """, (value, region["region_slug"]))

                habitats = cur.fetchall()

        return {
            "region": region,
            "habitats": habitats
        }

    except Exception as e:
        raise HTTPException(500, str(e))


# leave EVERYTHING else unchanged (your working endpoints)
