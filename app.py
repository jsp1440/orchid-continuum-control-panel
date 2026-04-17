import os
from typing import Any, Optional
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg
from psycopg.rows import dict_row

APP_TITLE = "Orchid Continuum Control Panel API"
APP_VERSION = "0.2.0"


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
                    CASE
                        WHEN f.image_url IS NOT NULL THEN true
                        ELSE false
                    END AS flower_preferred
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
    """
    Deterministic daily species spotlight.
    Picks one species per day from the eligible gallery pool, preferring species with
    multiple images and preferring flower images for the hero image when available.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 1) Pick the species for the day from the eligible pool.
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

                # 2) Pull up to max_thumbnails image URLs, preferring flower images.
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

                # 3) Optional atlas / country summary.
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

                # 4) Optional taxonomy / dossier / habitat enrichment using direct + normalized name paths.
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
                            2 AS priority
                        FROM public.orchid_taxonomy t
                        JOIN selected s
                          ON lower(trim(split_part(regexp_replace(COALESCE(NULLIF(t.accepted_scientific_name, ''), t.scientific_name), '\s*\(.*$', ''), ',', 1))) = s.normalized_name
                    ),
                    best_taxonomy AS (
                        SELECT *
                        FROM (
                            SELECT * FROM direct_taxonomy
                            UNION ALL
                            SELECT * FROM normalized_taxonomy
                        ) z
                        ORDER BY priority, accepted_name
                        LIMIT 1
                    ),
                    best_dossier AS (
                        SELECT
                            d.accepted_scientific_name,
                            d.accepted_scientific_name_html
                        FROM oc_taxonomy.species_dossier_v2 d
                        JOIN selected s
                          ON lower(trim(d.accepted_scientific_name)) = s.normalized_name
                        LIMIT 1
                    ),
                    best_habitat AS (
                        SELECT
                            h.canonical_name,
                            h.habitat_description,
                            h.light_conditions,
                            h.moisture_conditions,
                            h.min_elevation_m,
                            h.max_elevation_m
                        FROM oc_habitat.species_habitat_profile h
                        JOIN best_taxonomy bt
                          ON h.accepted_taxon_id = bt.id
                        LIMIT 1
                    )
                    SELECT
                        bt.id AS taxonomy_id,
                        bt.accepted_name,
                        bt.family AS taxonomy_family,
                        bt.genus AS taxonomy_genus,
                        bd.accepted_scientific_name_html,
                        bh.canonical_name AS habitat_name,
                        bh.habitat_description,
                        bh.light_conditions,
                        bh.moisture_conditions,
                        bh.min_elevation_m,
                        bh.max_elevation_m
                    FROM best_taxonomy bt
                    FULL OUTER JOIN best_dossier bd ON TRUE
                    FULL OUTER JOIN best_habitat bh ON TRUE
                    LIMIT 1
                    """,
                    {"scientific_name": scientific_name},
                )
                enrich_row = cur.fetchone()

        display_name = scientific_name
        habitat_text = None

        if enrich_row:
            if enrich_row.get("accepted_name"):
                display_name = enrich_row["accepted_name"]
            if enrich_row.get("accepted_scientific_name_html"):
                # Keep plain display_name simple; HTML can be exposed separately if needed later.
                pass

            habitat_bits = []
            if enrich_row.get("habitat_description"):
                habitat_bits.append(enrich_row["habitat_description"])
            if enrich_row.get("light_conditions"):
                habitat_bits.append(f"Light: {enrich_row['light_conditions']}")
            if enrich_row.get("moisture_conditions"):
                habitat_bits.append(f"Moisture: {enrich_row['moisture_conditions']}")
            if enrich_row.get("min_elevation_m") is not None or enrich_row.get("max_elevation_m") is not None:
                min_elev = enrich_row.get("min_elevation_m")
                max_elev = enrich_row.get("max_elevation_m")
                if min_elev is not None and max_elev is not None:
                    habitat_bits.append(f"Elevation: {int(to_json_number(min_elev))}–{int(to_json_number(max_elev))} m")
                elif min_elev is not None:
                    habitat_bits.append(f"Elevation: from {int(to_json_number(min_elev))} m")
                elif max_elev is not None:
                    habitat_bits.append(f"Elevation: up to {int(to_json_number(max_elev))} m")
            habitat_text = " | ".join(habitat_bits) if habitat_bits else None

        caption = build_caption(
            display_name=display_name,
            family=family,
            region=region,
            habitat=habitat_text,
            image_count=image_count,
        )

        return {
            "widget": "orchid_of_the_day",
            "display_name": display_name,
            "scientific_name": scientific_name,
            "genus": genus,
            "family": family,
            "hero_image_url": hero_image_url,
            "images": images,
            "caption": caption,
            "region": region,
            "habitat": habitat_text,
            "image_count": image_count,
        }

    except psycopg.errors.UndefinedTable as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "A required widget table or view is missing. Ensure these database objects exist: "
                "public.oc_species_display_gallery_view, public.oc_species_flower_gallery_view, "
                "public.orchid_taxonomy, oc_taxonomy.species_dossier_v2, "
                "oc_habitat.species_habitat_profile, oc_atlas.occurrences"
            ),
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Orchid of the day query failed: {exc}",
        ) from exc


@app.get("/api/orchid-atlas")
def orchid_atlas(
    mode: str = Query(default="species"),
    min_records: int = Query(default=1, ge=1),
    min_species: int = Query(default=1, ge=1),
    limit: int = Query(default=5000, ge=1, le=100000),
) -> dict[str, Any]:
    if mode not in {"species", "records"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid mode. Allowed values: 'species' or 'records'.",
        )

    order_sql = (
        "species_count DESC, records DESC"
        if mode == "species"
        else "records DESC, species_count DESC"
    )

    sql = f"""
        SELECT
            lat_band,
            lon_band,
            records,
            species_count,
            genus_count,
            min_elevation_m,
            max_elevation_m,
            country_count,
            first_record_at,
            last_record_at
        FROM public.orchid_atlas_layer
        WHERE records >= %(min_records)s
          AND species_count >= %(min_species)s
        ORDER BY {order_sql}, lat_band, lon_band
        LIMIT %(limit)s
    """

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    {
                        "min_records": min_records,
                        "min_species": min_species,
                        "limit": limit,
                    },
                )
                rows = cur.fetchall()

        cells = [
            {
                "lat": float(to_json_number(row["lat_band"])),
                "lon": float(to_json_number(row["lon_band"])),
                "records": int(to_json_number(row["records"])),
                "species_count": int(to_json_number(row["species_count"])),
                "genus_count": int(to_json_number(row["genus_count"])),
                "min_elevation_m": to_json_number(row["min_elevation_m"]),
                "max_elevation_m": to_json_number(row["max_elevation_m"]),
                "country_count": int(to_json_number(row["country_count"])),
                "first_record_at": (
                    row["first_record_at"].isoformat()
                    if row["first_record_at"] is not None
                    else None
                ),
                "last_record_at": (
                    row["last_record_at"].isoformat()
                    if row["last_record_at"] is not None
                    else None
                ),
            }
            for row in rows
        ]

        return {
            "ok": True,
            "mode": mode,
            "count": len(cells),
            "filters": {
                "min_records": min_records,
                "min_species": min_species,
                "limit": limit,
            },
            "cells": cells,
        }
    except psycopg.errors.UndefinedTable as exc:
        raise HTTPException(
            status_code=500,
            detail="Atlas layer is missing. Ensure these database objects exist: public.orchid_atlas_layer.",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Atlas query failed: {exc}") from exc


@app.get("/api/orchid-atlas/top")
def orchid_atlas_top(
    limit: int = Query(default=25, ge=1, le=500),
) -> dict[str, Any]:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        lat_band,
                        lon_band,
                        records,
                        species_count,
                        genus_count,
                        min_elevation_m,
                        max_elevation_m,
                        country_count,
                        first_record_at,
                        last_record_at
                    FROM public.orchid_atlas_layer
                    ORDER BY species_count DESC, records DESC, lat_band, lon_band
                    LIMIT %(limit)s
                    """,
                    {"limit": limit},
                )
                rows = cur.fetchall()

        return {
            "ok": True,
            "count": len(rows),
            "cells": [
                {
                    "lat": float(to_json_number(row["lat_band"])),
                    "lon": float(to_json_number(row["lon_band"])),
                    "records": int(to_json_number(row["records"])),
                    "species_count": int(to_json_number(row["species_count"])),
                    "genus_count": int(to_json_number(row["genus_count"])),
                    "min_elevation_m": to_json_number(row["min_elevation_m"]),
                    "max_elevation_m": to_json_number(row["max_elevation_m"]),
                    "country_count": int(to_json_number(row["country_count"])),
                    "first_record_at": (
                        row["first_record_at"].isoformat()
                        if row["first_record_at"] is not None
                        else None
                    ),
                    "last_record_at": (
                        row["last_record_at"].isoformat()
                        if row["last_record_at"] is not None
                        else None
                    ),
                }
                for row in rows
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Top atlas query failed: {exc}") from exc


@app.get("/api/orchid-atlas/geojson")
def orchid_atlas_geojson(
    mode: str = Query(default="species"),
    min_records: int = Query(default=1, ge=1),
    min_species: int = Query(default=1, ge=1),
    limit: int = Query(default=5000, ge=1, le=100000),
) -> JSONResponse:
    if mode not in {"species", "records"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid mode. Allowed values: 'species' or 'records'.",
        )

    order_sql = (
        "species_count DESC, records DESC"
        if mode == "species"
        else "records DESC, species_count DESC"
    )

    sql = f"""
        SELECT
            lat_band,
            lon_band,
            records,
            species_count,
            genus_count,
            min_elevation_m,
            max_elevation_m,
            country_count,
            first_record_at,
            last_record_at
        FROM public.orchid_atlas_layer
        WHERE records >= %(min_records)s
          AND species_count >= %(min_species)s
        ORDER BY {order_sql}, lat_band, lon_band
        LIMIT %(limit)s
    """

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    {
                        "min_records": min_records,
                        "min_species": min_species,
                        "limit": limit,
                    },
                )
                rows = cur.fetchall()

        features = []
        for row in rows:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [
                            float(to_json_number(row["lon_band"])),
                            float(to_json_number(row["lat_band"])),
                        ],
                    },
                    "properties": {
                        "records": int(to_json_number(row["records"])),
                        "species_count": int(to_json_number(row["species_count"])),
                        "genus_count": int(to_json_number(row["genus_count"])),
                        "min_elevation_m": to_json_number(row["min_elevation_m"]),
                        "max_elevation_m": to_json_number(row["max_elevation_m"]),
                        "country_count": int(to_json_number(row["country_count"])),
                        "first_record_at": (
                            row["first_record_at"].isoformat()
                            if row["first_record_at"] is not None
                            else None
                        ),
                        "last_record_at": (
                            row["last_record_at"].isoformat()
                            if row["last_record_at"] is not None
                            else None
                        ),
                    },
                }
            )

        return JSONResponse(
            content={
                "type": "FeatureCollection",
                "mode": mode,
                "count": len(features),
                "features": features,
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Atlas GeoJSON query failed: {exc}") from exc
