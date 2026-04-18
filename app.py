import os
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import psycopg

app = FastAPI()

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    return psycopg.connect(DATABASE_URL)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/db/ping")
def db_ping():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------
# REGION PROFILE
# ---------------------------------------------------
@app.get("/api/orchid-widgets/region-profile")
def region_profile(value: str = Query(...)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM oc_regions.region_profiles
                WHERE lower(region_name) = lower(%s)
                   OR lower(region_slug) = lower(%s)
                LIMIT 1
            """, (value, value))

            row = cur.fetchone()
            if not row:
                return {"widget": "region_profile", "error": "Region not found"}

            columns = [desc[0] for desc in cur.description]
            data = dict(zip(columns, row))

            return {
                "widget": "region_profile",
                "region": data
            }


# ---------------------------------------------------
# FEATURED GALLERY
# ---------------------------------------------------
@app.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(limit: int = 3):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, scientific_name, display_name, hero_image_url
                FROM oc_species.species
                WHERE hero_image_url IS NOT NULL
                ORDER BY random()
                LIMIT %s
            """, (limit,))

            rows = cur.fetchall()

            cards = []
            for r in rows:
                cards.append({
                    "id": r[0],
                    "scientific_name": r[1],
                    "display_name": r[2],
                    "hero_image_url": r[3]
                })

            return {
                "widget": "featured_gallery",
                "count": len(cards),
                "cards": cards
            }


# ---------------------------------------------------
# ORCHIDS BY REGION (FIXED VERSION)
# ---------------------------------------------------
@app.get("/api/orchid-widgets/orchids-by-region")
def orchids_by_region(
    scope: str = Query(...),
    value: str = Query(...),
    limit: int = 50
):
    scope = scope.lower()

    with get_conn() as conn:
        with conn.cursor() as cur:

            # -----------------------------------------
            # COUNTRY (DIRECT)
            # -----------------------------------------
            if scope == "country":
                cur.execute("""
                    SELECT id, scientific_name, display_name, hero_image_url, country
                    FROM public.orchid_occurrence
                    WHERE lower(country) = lower(%s)
                    LIMIT %s
                """, (value, limit))

                rows = cur.fetchall()

                return format_response(scope, value, rows)

            # -----------------------------------------
            # REGION / CONTINENT / ISLAND (NEW LOGIC)
            # -----------------------------------------
            cur.execute("""
                SELECT country_name
                FROM oc_regions.region_country_members
                WHERE lower(region_slug) = lower(%s)
                   OR lower(region_slug) = lower(replace(%s, ' ', '-'))
            """, (value, value))

            countries = [r[0] for r in cur.fetchall()]

            if not countries:
                return {
                    "widget": "orchids_by_region",
                    "scope": scope,
                    "value": value,
                    "count": 0,
                    "orchids": [],
                    "mapping_note": "No country membership defined for this region"
                }

            # Query occurrences via country membership
            cur.execute(f"""
                SELECT id, scientific_name, display_name, hero_image_url, country
                FROM public.orchid_occurrence
                WHERE country = ANY(%s)
                LIMIT %s
            """, (countries, limit))

            rows = cur.fetchall()

            return format_response(scope, value, rows)


# ---------------------------------------------------
# RESPONSE FORMATTER
# ---------------------------------------------------
def format_response(scope, value, rows):
    orchids = []

    for r in rows:
        orchids.append({
            "id": r[0],
            "scientific_name": r[1],
            "display_name": r[2],
            "hero_image_url": r[3],
            "matched_value": r[4]
        })

    return {
        "widget": "orchids_by_region",
        "scope": scope,
        "value": value,
        "count": len(orchids),
        "orchids": orchids
    }
