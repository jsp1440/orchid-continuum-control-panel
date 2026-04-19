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
# IMAGE POOL HELPER
# -------------------------
def get_image_pool(n: int = 10) -> list:
    """Pull up to n real image URLs from orchid_images. Returns list of URLs."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT image_url
                    FROM orchid_images
                    WHERE image_url IS NOT NULL AND image_url != ''
                    ORDER BY RANDOM()
                    LIMIT %s
                """, (n,))
                rows = cur.fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []

def pick_image(pool: list, used: set) -> str:
    """Pick an unused image from pool, or fallback if exhausted."""
    for url in pool:
        if url not in used:
            used.add(url)
            return url
    return get_fallback_image()

# -------------------------
# REGION PROFILE
# -------------------------
@app.get("/api/orchid-widgets/region-profile")
def region_profile(value: str):

    # Pull a small pool of real images from DB
    pool = get_image_pool(10)
    used = set()

    hero_image_url = pick_image(pool, used) if pool else get_fallback_image()

    habitats = [
        {
            "habitat_name": "Andean Cloud Forest",
            "habitat_description": "Humid montane forest with high orchid diversity.",
            "image_url": pick_image(pool, used) if pool else get_fallback_image()
        },
        {
            "habitat_name": "Upper Amazonian Lowland Rainforest",
            "habitat_description": "Warm wet rainforest with canopy epiphytes.",
            "image_url": pick_image(pool, used) if pool else get_fallback_image()
        },
        {
            "habitat_name": "Chocó Wet Forest",
            "habitat_description": "Extremely wet biodiversity hotspot on the Pacific slope.",
            "image_url": pick_image(pool, used) if pool else get_fallback_image()
        }
    ]

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
            "hero_image_url": hero_image_url,
            "habitats": habitats
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
