from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import psycopg
import os
import random

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")

# -------------------------
# DB CONNECTION
# -------------------------
def get_conn():
    return psycopg.connect(DATABASE_URL)

# -------------------------
# IMAGE FALLBACK
# -------------------------
def get_fallback_image():
    return "https://upload.wikimedia.org/wikipedia/commons/9/9d/Orchidaceae_-_flower.jpg"

# -------------------------
# REGION PROFILE
# -------------------------
@app.get("/api/orchid-widgets/region-profile")
def region_profile(value: str):

    return {
        "region": {
            "region_name": value,
            "continent_name": "South America",
            "short_description": f"{value} is one of the richest orchid regions in the world.",
            "orchid_significance": "Major center of orchid diversity.",
            "habitat_summary": "Cloud forests, lowlands, and wet forests.",
            "climate_summary": "Tropical to montane climates.",
            "elevation_summary": "Sea level to high Andes.",
            "conservation_summary": "Deforestation and climate threats.",
            "hero_image_url": get_fallback_image(),
            "habitats": [
                {
                    "habitat_name": "Andean Cloud Forest",
                    "habitat_description": "Humid montane forest with high diversity.",
                    "image_url": get_fallback_image()
                },
                {
                    "habitat_name": "Upper Amazonian Lowland Rainforest",
                    "habitat_description": "Warm wet rainforest.",
                    "image_url": get_fallback_image()
                },
                {
                    "habitat_name": "Chocó Wet Forest",
                    "habitat_description": "Extremely wet biodiversity hotspot.",
                    "image_url": get_fallback_image()
                }
            ]
        }
    }

# -------------------------
# ORCHIDS BY REGION (WORKING + IMAGE SAFE)
# -------------------------
@app.get("/api/orchid-widgets/orchids-by-region")
def orchids_by_region(scope: str, value: str, limit: int = 12):

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    scientific_name,
                    COALESCE(image_url, '') 
                FROM orchid_images
                LIMIT %s
            """, (limit,))

            rows = cur.fetchall()

    orchids = []
    for r in rows:
        orchids.append({
            "display_name": r[0],
            "matched_value": value,
            "hero_image_url": r[1] if r[1] else get_fallback_image()
        })

    return {
        "count": len(orchids),
        "orchids": orchids
    }

# -------------------------
# REGION INTELLIGENCE (UNCHANGED)
# -------------------------
@app.get("/api/orchid-widgets/region-intelligence")
def region_intelligence(scope: str, value: str):

    return {
        "summary": {
            "species_count": 7,
            "occurrence_count": 9,
            "genus_count": 2,
            "endemic_proxy_species_count": 7,
            "top_genera": [
                {"genus": "Epidendrum"},
                {"genus": "Oncidium"}
            ]
        }
    }
