# FILE: app.py
# AFFECTS: restores region intelligence for Atlas and filters orchid card images to prefer live plant photos over herbarium/specimen scans
# WILL NOT BREAK: /health, /db/ping, /atlas.html, region-profile loading, featured gallery loading, orchids-by-region loading, existing database tables/schema

#!/usr/bin/env python3

import os
from pathlib import Path

import psycopg
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from psycopg.rows import dict_row

APP_TITLE = "Orchid Continuum API"
APP_VERSION = "1.8"

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
        raise RuntimeError("Could not find a usable taxonomy name column in orchid_taxonomy")

    if len(candidates) == 1:
        return candidates[0]

    return f"COALESCE({', '.join(candidates)})"


def find_atlas_html() -> Path:
    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / "atlas.html",
        base_dir / "static" / "atlas.html",
        base_dir / "templates" / "atlas.html",
    ]

    for path in candidates:
        if path.exists() and path.is_file():
            return path

    raise HTTPException(
        status_code=404,
        detail="atlas.html not found in app root, static/, or templates/",
    )


def image_score_sql(url_expr: str) -> str:
    """
    Lower score = better image for public-facing atlas cards.
    0 = likely live plant photo
    1 = acceptable generic image
    9 = likely herbarium/specimen/scan
    """
    u = f"lower(coalesce({url_expr}, ''))"
    return f"""
    CASE
        WHEN {u} = '' THEN 99

        WHEN {u} LIKE '%inaturalist%' THEN 0
        WHEN {u} LIKE '%static.inaturalist.org%' THEN 0
        WHEN {u} LIKE '%plantnet%' THEN 0
        WHEN {u} LIKE '%flickr%' THEN 0
        WHEN {u} LIKE '%commons.wikimedia%' THEN 0
        WHEN {u} LIKE '%upload.wikimedia%' THEN 0
        WHEN {u} LIKE '%orchidspecies%' THEN 0
        WHEN {u} LIKE '%instagram%' THEN 0

        WHEN {u} LIKE '%herbarium%' THEN 9
        WHEN {u} LIKE '%specimen%' THEN 9
        WHEN {u} LIKE '%type%' THEN 9
        WHEN {u} LIKE '%huh.harvard.edu%' THEN 9
        WHEN {u} LIKE '%data.huh.harvard.edu%' THEN 9
        WHEN {u} LIKE '%sernec%' THEN 9
        WHEN {u} LIKE '%jstor%' THEN 9
        WHEN {u} LIKE '%museum%' THEN 9
        WHEN {u} LIKE '%collection%' THEN 9
        WHEN {u} LIKE '%collections%' THEN 9
        WHEN {u} LIKE '%mnhn%' THEN 9
        WHEN {u} LIKE '%tropicos%' THEN 9
        WHEN {u} LIKE '%mobot.org%' THEN 9
        WHEN {u} LIKE '%preserved%' THEN 9
        WHEN {u} LIKE '%scan%' THEN 9
        WHEN {u} LIKE '%sheet%' THEN 9
        ELSE 1
    END
    """


def best_region_live_image(conn, country_name: str | None) -> str | None:
    if not country_name:
        return None

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH ranked AS (
                SELECT
                    i.image_url,
                    {image_score_sql("i.image_url")} AS score,
                    row_number() OVER (
                        ORDER BY
                            {image_score_sql("i.image_url")},
                            i.image_url
                    ) AS rn
                FROM public.orchid_occurrence o
                JOIN public.orchid_images i
                  ON i.taxonomy_id = o.taxonomy_id
                WHERE lower(coalesce(o.country, '')) = lower(%s)
                  AND i.image_url IS NOT NULL
            )
            SELECT image_url
            FROM ranked
            WHERE rn = 1
              AND score < 9
            """,
            (country_name,),
        )
        row = cur.fetchone()
        return row["image_url"] if row else None


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


@app.get("/atlas.html")
def serve_atlas_html():
    atlas_path = find_atlas_html()
    return FileResponse(atlas_path, media_type="text/html")


@app.get("/api/orchid-widgets/region")
def region_legacy(
    scope: str = Query(..., description="country | region | island | continent"),
    value: str = Query(..., description="Ecuador | Borneo | South America"),
):
    return region_profile(value=value)


@app.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(
    limit: int = Query(default=12, ge=1, le=48),
    randomize: bool = Query(default=False),
):
    try:
        with get_conn() as conn:
            tax_cols = fetch_columns(conn, "public", "orchid_taxonomy")
            name_expr = build_taxonomy_name_expr(tax_cols)
            order_clause = "random()" if randomize else "scientific_name ASC"

            sql = f"""
            WITH ranked_images AS (
                SELECT
                    i.taxonomy_id,
                    i.image_url,
                    {image_score_sql("i.image_url")} AS score,
                    row_number() OVER (
                        PARTITION BY i.taxonomy_id
                        ORDER BY
                            {image_score_sql("i.image_url")},
                            i.image_url
                    ) AS rn
                FROM public.orchid_images i
                WHERE i.image_url IS NOT NULL
            ),
            best_images AS (
                SELECT taxonomy_id, image_url
                FROM ranked_images
                WHERE rn = 1
                  AND score < 9
            )
            SELECT
                t.id,
                {name_expr} AS scientific_name,
                b.image_url AS hero_image_url
            FROM best_images b
            JOIN public.orchid_taxonomy t
              ON t.id = b.taxonomy_id
            WHERE b.image_url IS NOT NULL
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
                    )
                    SELECT *
                    FROM target
                    """,
                    (value, value, value),
                )
                region = cur.fetchone()

                if not region:
                    raise HTTPException(status_code=404, detail="Region not found")

                country_name = region.get("country_name") or region.get("region_name")
                fallback_live_image = best_region_live_image(conn, country_name)

                cur.execute(
                    """
                    SELECT
                        habitat_name,
                        habitat_description,
                        image_url,
                        image_caption,
                        sort_order
                    FROM oc_regions.region_habitats
                    WHERE region_slug = %s
                    ORDER BY sort_order
                    """,
                    (region["region_slug"],),
                )
                habitats = cur.fetchall()

                cur.execute(
                    """
                    SELECT
                        media_type,
                        media_url,
                        caption,
                        credit,
                        sort_order
                    FROM oc_regions.region_media
                    WHERE region_slug = %s
                    ORDER BY sort_order
                    """,
                    (region["region_slug"],),
                )
                media = cur.fetchall()

        region = dict(region)
        region["hero_image_url"] = region.get("hero_image_url") or fallback_live_image

        fixed_habitats = []
        for h in habitats:
            item = dict(h)
            item["image_url"] = item.get("image_url") or fallback_live_image
            fixed_habitats.append(item)

        region["habitats"] = fixed_habitats
        region["media"] = [dict(m) for m in media]

        return {
            "widget": "region_profile",
            "region": region,
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
            occ_cols = fetch_columns(conn, "public", "orchid_occurrence")
            required_occ = {"taxonomy_id", "country", "region", "scientific_name"}
            missing = sorted(required_occ - occ_cols)
            if missing:
                raise RuntimeError(
                    f"public.orchid_occurrence is missing required columns: {', '.join(missing)}"
                )

            image_cte = f"""
            WITH ranked_images AS (
                SELECT
                    i.taxonomy_id,
                    i.image_url,
                    {image_score_sql("i.image_url")} AS score,
                    row_number() OVER (
                        PARTITION BY i.taxonomy_id
                        ORDER BY
                            {image_score_sql("i.image_url")},
                            i.image_url
                    ) AS rn
                FROM public.orchid_images i
                WHERE i.image_url IS NOT NULL
            ),
            best_images AS (
                SELECT taxonomy_id, image_url
                FROM ranked_images
                WHERE rn = 1
                  AND score < 9
            )
            """

            if normalized_scope == "country":
                sql = image_cte + """
                SELECT
                    o.taxonomy_id AS id,
                    COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid') AS scientific_name,
                    MIN(o.country) AS matched_value,
                    MIN(b.image_url) AS hero_image_url
                FROM public.orchid_occurrence o
                JOIN best_images b
                  ON b.taxonomy_id = o.taxonomy_id
                WHERE lower(COALESCE(o.country, '')) = lower(%s)
                GROUP BY o.taxonomy_id, COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid')
                ORDER BY scientific_name
                LIMIT %s
                """
                params = (value, limit)
                match_strategy = "direct_country_live_only"

            elif normalized_scope in {"region", "island"}:
                sql = image_cte + """
                SELECT
                    o.taxonomy_id AS id,
                    COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid') AS scientific_name,
                    MIN(COALESCE(o.region, o.country)) AS matched_value,
                    MIN(b.image_url) AS hero_image_url
                FROM public.orchid_occurrence o
                JOIN best_images b
                  ON b.taxonomy_id = o.taxonomy_id
                WHERE (
                        lower(COALESCE(o.region, '')) = lower(%s)
                     OR lower(COALESCE(o.country, '')) = lower(%s)
                     OR lower(COALESCE(o.country, '')) IN (
                        SELECT lower(country_name)
                        FROM oc_regions.region_country_members
                        WHERE lower(region_slug) = lower(%s)
                     )
                )
                GROUP BY o.taxonomy_id, COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid')
                ORDER BY scientific_name
                LIMIT %s
                """
                params = (value, value, value, limit)
                match_strategy = "direct_region_country_or_curated_membership_live_only"

            else:
                continent_slug = value.strip().lower().replace(" ", "-")
                sql = image_cte + """
                SELECT
                    o.taxonomy_id AS id,
                    COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid') AS scientific_name,
                    MIN(o.country) AS matched_value,
                    MIN(b.image_url) AS hero_image_url
                FROM public.orchid_occurrence o
                JOIN best_images b
                  ON b.taxonomy_id = o.taxonomy_id
                WHERE lower(COALESCE(o.country, '')) IN (
                    SELECT lower(rcm.country_name)
                    FROM oc_regions.region_country_members rcm
                    WHERE lower(rcm.region_slug) = lower(%s)
                )
                GROUP BY o.taxonomy_id, COALESCE(NULLIF(o.scientific_name, ''), 'Unknown orchid')
                ORDER BY scientific_name
                LIMIT %s
                """
                params = (continent_slug, limit)
                match_strategy = "continent_via_region_country_members_live_only"

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

        if len(orchids) == 0:
            response["mapping_note"] = "No live-photo orchid matches were found for this query."

        return response

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Orchids by region failed: {exc}")


@app.get("/api/orchid-widgets/region-intelligence")
def region_intelligence(
    scope: str = Query(..., description="country | continent"),
    value: str = Query(..., description="Brazil | South America"),
):
    try:
        normalized_scope = scope.strip().lower()
        if normalized_scope not in {"country", "continent"}:
            raise HTTPException(status_code=400, detail="scope must be one of: country, continent")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        region_scope,
                        region_name,
                        parent_region_name,
                        occurrence_count,
                        species_count,
                        taxonomy_count,
                        genus_count,
                        endemic_proxy_species_count,
                        min_elevation_m,
                        max_elevation_m,
                        avg_elevation_m,
                        georeferenced_occurrence_count,
                        dominant_climate_preference,
                        dominant_growth_habit,
                        top_genera,
                        created_at
                    FROM oc_intelligence.v_region_species_summary
                    WHERE lower(region_scope) = lower(%s)
                      AND lower(region_name) = lower(%s)
                    LIMIT 1
                    """,
                    (normalized_scope, value),
                )
                row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Region intelligence not found")

        return {
            "widget": "region_intelligence",
            "scope": normalized_scope,
            "value": value,
            "summary": row,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Region intelligence failed: {exc}")


@app.get("/api/orchid-widgets/top-regions")
def top_regions(
    scope: str = Query(..., description="country | continent"),
    sort_by: str = Query(default="species_count", description="species_count | occurrence_count"),
    limit: int = Query(default=10, ge=1, le=100),
):
    try:
        normalized_scope = scope.strip().lower()
        if normalized_scope not in {"country", "continent"}:
            raise HTTPException(status_code=400, detail="scope must be one of: country, continent")

        allowed_sorts = {"species_count", "occurrence_count"}
        if sort_by not in allowed_sorts:
            raise HTTPException(status_code=400, detail="sort_by must be one of: species_count, occurrence_count")

        order_clause = (
            "species_count DESC, occurrence_count DESC, region_name"
            if sort_by == "species_count"
            else "occurrence_count DESC, species_count DESC, region_name"
        )

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        region_scope,
                        region_name,
                        occurrence_count,
                        species_count,
                        taxonomy_count,
                        genus_count,
                        endemic_proxy_species_count,
                        min_elevation_m,
                        max_elevation_m,
                        avg_elevation_m,
                        georeferenced_occurrence_count,
                        dominant_climate_preference,
                        dominant_growth_habit,
                        top_genera,
                        created_at
                    FROM oc_intelligence.v_region_species_summary
                    WHERE lower(region_scope) = lower(%s)
                    ORDER BY {order_clause}
                    LIMIT %s
                    """,
                    (normalized_scope, limit),
                )
                rows = cur.fetchall()

        return {
            "widget": "top_regions",
            "scope": normalized_scope,
            "sort_by": sort_by,
            "count": len(rows),
            "rows": rows,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Top regions failed: {exc}")
