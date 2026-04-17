import os
from typing import Any, Optional
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import psycopg
from psycopg.rows import dict_row

APP_TITLE = "Orchid Continuum Control Panel API"
APP_VERSION = "0.3.0"


# -----------------------------
# DATABASE
# -----------------------------
def get_database_url() -> str:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    return db_url


def get_conn() -> psycopg.Connection:
    return psycopg.connect(get_database_url(), row_factory=dict_row)


def to_json_number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


# -----------------------------
# APP INIT
# -----------------------------
app = FastAPI(title=APP_TITLE, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# BASIC ENDPOINTS
# -----------------------------
@app.get("/")
def root():
    return {"service": APP_TITLE, "version": APP_VERSION, "status": "running"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/db/ping")
def db_ping():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 as ok")
            return {"ok": True}


# -----------------------------
# FEATURED GALLERY (UNCHANGED)
# -----------------------------
@app.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(limit: int = 12):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        scientific_name,
                        genus,
                        family,
                        image_url,
                        image_count
                    FROM public.oc_species_display_gallery_view
                    ORDER BY image_count DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()

        return {
            "widget": "featured_gallery",
            "cards": [
                {
                    "scientific_name": r["scientific_name"],
                    "genus": r["genus"],
                    "family": r["family"],
                    "hero_image_url": r["image_url"],
                    "image_count": int(to_json_number(r["image_count"])),
                }
                for r in rows
            ],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# ORCHID OF THE DAY (NEW)
# -----------------------------
@app.get("/api/orchid-widgets/orchid-of-the-day")
def orchid_of_the_day():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:

                # deterministic daily selection
                cur.execute(
                    """
                    WITH ranked AS (
                        SELECT
                            scientific_name,
                            genus,
                            family,
                            COUNT(*) AS image_count
                        FROM public.oc_species_display_gallery_view
                        GROUP BY scientific_name, genus, family
                        HAVING COUNT(*) >= 5
                    )
                    SELECT *
                    FROM ranked
                    ORDER BY md5(current_date::text || scientific_name)
                    LIMIT 1
                    """
                )

                orchid = cur.fetchone()

                if not orchid:
                    raise HTTPException(status_code=404, detail="No orchid found")

                name = orchid["scientific_name"]

                # get images
                cur.execute(
                    """
                    SELECT image_url
                    FROM public.oc_species_display_gallery_view
                    WHERE scientific_name = %s
                    LIMIT 9
                    """,
                    (name,),
                )

                images = [r["image_url"] for r in cur.fetchall() if r["image_url"]]

                hero = images[0] if images else None

        return {
            "widget": "orchid_of_the_day",
            "scientific_name": name,
            "genus": orchid["genus"],
            "family": orchid["family"],
            "hero_image_url": hero,
            "images": images,
            "image_count": int(to_json_number(orchid["image_count"])),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# ATLAS (BASIC)
# -----------------------------
@app.get("/api/orchid-atlas")
def orchid_atlas(limit: int = 1000):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        lat,
                        lon,
                        scientific_name
                    FROM public.orchid_occurrence
                    WHERE lat IS NOT NULL
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()

        return {"points": rows}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# ATLAS GEOJSON
# -----------------------------
@app.get("/api/orchid-atlas/geojson")
def atlas_geojson(limit: int = 1000):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT lat, lon, scientific_name
                    FROM public.orchid_occurrence
                    WHERE lat IS NOT NULL
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()

        features = []
        for r in rows:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [r["lon"], r["lat"]],
                    },
                    "properties": {
                        "name": r["scientific_name"],
                    },
                }
            )

        return {"type": "FeatureCollection", "features": features}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
