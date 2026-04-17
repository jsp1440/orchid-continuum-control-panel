import os
from typing import Any, Optional
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg
from psycopg.rows import dict_row

APP_TITLE = "Orchid Continuum Control Panel API"
APP_VERSION = "0.2.1"


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


def build_caption(
    display_name: str,
    family: Optional[str],
    region: Optional[str],
    habitat: Optional[str],
    image_count: int,
) -> str:
    parts = [f"{display_name} is featured today from the Orchid Continuum gallery."]
    if family:
        parts.append(f"It belongs to the family {family}.")
    if region:
        parts.append(f"Occurrence data currently point to {region}.")
    if habitat:
        parts.append(f"Habitat notes: {habitat}")
    parts.append(f"The gallery currently includes {image_count} image{'s' if image_count != 1 else ''} for this orchid.")
    return " ".join(parts)


app = FastAPI(title=APP_TITLE, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": APP_TITLE,
        "version": APP_VERSION,
        "status": "running",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": APP_TITLE,
        "version": APP_VERSION,
    }


@app.get("/system/status")
def system_status() -> dict[str, Any]:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                row = cur.fetchone()
                db_ok = bool(row and row["ok"] == 1)

        return {
            "ok": True,
            "service": APP_TITLE,
            "version": APP_VERSION,
            "status": "running",
            "database": "connected" if db_ok else "error",
        }
    except Exception as exc:
        return {
            "ok": False,
            "service": APP_TITLE,
            "version": APP_VERSION,
            "status": "running",
            "database": "error",
            "detail": str(exc),
        }


@app.get("/db/ping")
def db_ping() -> dict[str, Any]:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_database()::text AS database,
                        current_schema()::text AS schema,
                        current_user::text AS db_user
                    """
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("Database ping returned no row")

                return {
                    "ok": True,
                    "database": row["database"],
                    "schema": row["schema"],
                    "db_user": row["db_user"],
                }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database ping failed: {exc}") from exc


@app.get("/api/orchid-widgets/featured-gallery")
def featured_orchid_gallery(
    limit: int = Query(default=12, ge=1, le=48),
    genus: Optional[str] = Query(default=None),
    randomize: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        order_sql = "RANDOM()" if randomize else "image_count DESC, scientific_name ASC"

        sql = f"""
            WITH flower_ranked AS (
                SELECT
                    g.scientific_name,
                    g.genus,
                    g.family,
                    g.image_url,
                    g.image_type,
                    g.is_primary,
                    g.image_rank,
                    COUNT(*) OVER (PARTITION BY g.scientific_name) AS image_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY g.scientific_name
                        ORDER BY
                            CASE WHEN g.is_primary THEN 0 ELSE 1 END,
                            g.image_rank ASC NULLS LAST,
                            g.image_url
                    ) AS rn
                FROM public.oc_species_flower_gallery_view g
                WHERE (%(genus)s::text IS NULL OR g.genus ILIKE %(genus_pattern)s)
            ),
            display_ranked AS (
                SELECT
                    g.scientific_name,
                    g.genus,
                    g.family,
                    g.image_url,
                    g.image_type,
                    g.is_primary,
                    g.image_rank,
                    COUNT(*) OVER (PARTITION BY g.scientific_name) AS image_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY g.scientific_name
                        ORDER BY
                            CASE WHEN g.is_primary THEN 0 ELSE 1 END,
                            g.image_rank ASC NULLS LAST,
                            g.image_url
                    ) AS rn
                FROM public.oc_species_display_gallery_view g
                WHERE (%(genus)s::text IS NULL OR g.genus ILIKE %(genus_pattern)s)
            ),
            merged AS (
                SELECT
                    d.scientific_name,
                    d.genus,
                    d.family,
                    COALESCE(f.image_url, d.image_url) AS hero_image_url,
                    COALESCE(f.image_type, d.image_type) AS image_type,
                    COALESCE(f.image_count, d.image_count) AS image_count,
                    CASE WHEN f.image_url IS NOT NULL THEN true ELSE false END AS flower_preferred
                FROM (
                    SELECT *
                    FROM display_ranked
                    WHERE rn = 1
                ) d
                LEFT JOIN (
                    SELECT *
                    FROM flower_ranked
                    WHERE rn = 1
                ) f
                  ON d.scientific_name = f.scientific_name
            )
            SELECT
                scientific_name,
                genus,
                family,
                hero_image_url,
                image_type,
                image_count,
                flower_preferred
            FROM merged
            ORDER BY {order_sql}
            LIMIT %(limit)s
        """

        params = {
            "limit": limit,
            "genus": genus,
            "genus_pattern": f"%{genus}%" if genus else None,
        }

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        cards = [
            {
                "scientific_name": row["scientific_name"],
                "display_name": row["scientific_name"],
                "genus": row["genus"],
                "family": row["family"],
                "hero_image_url": row["hero_image_url"],
                "image_type": row["image_type"],
                "image_count": int(to_json_number(row["image_count"])),
                "atlas_available": True,
                "flower_preferred": bool(row["flower_preferred"]),
            }
            for row in rows
        ]

        return {
            "widget": "featured_gallery",
            "count": len(cards),
            "filters": {
                "genus": genus,
                "randomize": randomize,
            },
            "cards": cards,
        }

    except psycopg.errors.UndefinedTable as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "A required gallery view is missing. Ensure these database objects exist: "
                "public.oc_species_display_gallery_view and public.oc_species_flower_gallery_view"
            ),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Featured gallery query failed: {exc}",
        ) from exc


@app.get("/api/orchid-widgets/orchid-of-the-day")
def orchid_of_the_day(
    min_images: int = Query(default=5, ge=3, le=50),
    max_thumbnails: int = Query(default=9, ge=1, le=12),
) -> dict[str, Any]:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH species_counts AS (
                        SELECT
                            scientific_name,
                            MIN(genus) AS genus,
                            MIN(family) AS family,
                            COUNT(*) AS image_count
                        FROM public.oc_species_display_gallery_view
                        GROUP BY scientific_name
                        HAVING COUNT(*) >= %(min_images)s
                    )
                    SELECT
                        scientific_name,
                        genus,
                        family,
                        image_count
                    FROM species_counts
                    ORDER BY md5(current_date::text || scientific_name)
                    LIMIT 1
                    """,
                    {"min_images": min_images},
                )
                species_row = cur.fetchone()

                if not species_row:
                    raise HTTPException(
                        status_code=404,
                        detail="No eligible orchid-of-the-day species found.",
                    )

                scientific_name = species_row["scientific_name"]
                genus = species_row["genus"]
                family = species_row["family"]
                image_count = int(to_json_number(species_row["image_count"]))

                cur.execute(
                    """
                    WITH flower_images AS (
                        SELECT
                            image_url,
                            image_type,
                            is_primary,
                            image_rank,
                            0 AS source_priority
                        FROM public.oc_species_flower_gallery_view
                        WHERE scientific_name = %(scientific_name)s
                    ),
                    display_images AS (
                        SELECT
                            image_url,
                            image_type,
                            is_primary,
                            image_rank,
                            1 AS source_priority
                        FROM public.oc_species_display_gallery_view
                        WHERE scientific_name = %(scientific_name)s
                    ),
                    merged AS (
                        SELECT DISTINCT ON (image_url)
                            image_url,
                            image_type,
                            is_primary,
                            image_rank,
                            source_priority
                        FROM (
                            SELECT * FROM flower_images
                            UNION ALL
                            SELECT * FROM display_images
                        ) x
                        WHERE image_url IS NOT NULL
                        ORDER BY image_url, source_priority, is_primary DESC, image_rank ASC NULLS LAST
                    )
                    SELECT
                        image_url,
                        image_type,
                        is_primary,
                        image_rank,
                        source_priority
                    FROM merged
                    ORDER BY
                        source_priority ASC,
                        CASE WHEN is_primary THEN 0 ELSE 1 END,
                        image_rank ASC NULLS LAST,
                        image_url
                    LIMIT %(max_thumbnails)s
                    """,
                    {
                        "scientific_name": scientific_name,
                        "max_thumbnails": max_thumbnails,
                    },
                )
                image_rows = cur.fetchall()

                images = [row["image_url"] for row in image_rows if row["image_url"]]
                hero_image_url = images[0] if images else None

                if not hero_image_url:
                    raise HTTPException(
                        status_code=404,
                        detail="No images found for orchid-of-the-day species.",
                    )

                cur.execute(
                    """
                    WITH occ AS (
                        SELECT country
                        FROM oc_atlas.occurrences
                        WHERE scientific_name = %(scientific_name)s
                          AND country IS NOT NULL
                    )
                    SELECT
                        string_agg(country, ', ' ORDER BY country) AS countries
                    FROM (
                        SELECT country
                        FROM occ
                        GROUP BY country
                        ORDER BY COUNT(*) DESC, country
                        LIMIT 3
                    ) top_countries
                    """,
                    {"scientific_name": scientific_name},
                )
                occ_row = cur.fetchone()
                region = occ_row["countries"] if occ_row and occ_row["countries"] else None

                cur.execute(
                    """
                    WITH selected AS (
                        SELECT
                            %(scientific_name)s::text AS gallery_scientific_name,
                            lower(trim(split_part(regexp_replace(%(scientific_name)s::text, '\s*\(.*$', ''), ',', 1))) AS normalized_name
                    ),
                    direct_taxonomy AS (
                        SELECT
                            t.id,
                            COALESCE(NULLIF(t.accepted_scientific_name, ''), t.scientific_name) AS accepted_name,
                            t.family,
                            t.genus,
                            1 AS priority
                        FROM public.orchid_taxonomy t
                        JOIN selected s
                          ON lower(trim(COALESCE(NULLIF(t.accepted_scientific_name, ''), t.scientific_name))) = lower(trim(s.gallery_scientific_name))
                    ),
                    normalized_taxonomy AS (
                        SELECT
                            t.id,
                            COALESCE(NULLIF(t.accepted_scientific_name, ''), t.scientific_name) AS accepted_name,
                            t.family,
                            t.genus,
                            2 AS
