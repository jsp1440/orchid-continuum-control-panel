# FILE: app.py
# AFFECTS: adds homepage compatibility endpoints and first dossier API foundation
# WILL NOT BREAK: /health, /db/ping, /atlas.html, existing orchid widget endpoints

#!/usr/bin/env python3

import os
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from psycopg.rows import dict_row

APP_TITLE = "Orchid Continuum API"
APP_VERSION = "1.11"

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


def table_exists(conn, schema_name: str, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
            ) AS exists
            """,
            (schema_name, table_name),
        )
        return bool(cur.fetchone()["exists"])


def fetch_columns(conn, schema_name: str, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema_name, table_name),
        )
        return {row["column_name"] for row in cur.fetchall()}


def scalar(conn, sql: str, params: tuple[Any, ...] = ()) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return None
        return next(iter(row.values()))


def find_atlas_html() -> Path:
    base_dir = Path(__file__).resolve().parent
    for path in [base_dir / "atlas.html", base_dir / "static" / "atlas.html", base_dir / "templates" / "atlas.html"]:
        if path.exists() and path.is_file():
            return path
    raise HTTPException(status_code=404, detail="atlas.html not found")


def image_score_sql(url_expr: str) -> str:
    u = f"lower(coalesce({url_expr}, ''))"
    return f"""
    CASE
        WHEN {u} = '' THEN 99
        WHEN {u} LIKE '%%inaturalist%%' THEN 0
        WHEN {u} LIKE '%%static.inaturalist.org%%' THEN 0
        WHEN {u} LIKE '%%plantnet%%' THEN 0
        WHEN {u} LIKE '%%flickr%%' THEN 0
        WHEN {u} LIKE '%%commons.wikimedia%%' THEN 0
        WHEN {u} LIKE '%%upload.wikimedia%%' THEN 0
        WHEN {u} LIKE '%%orchidspecies%%' THEN 0
        WHEN {u} LIKE '%%herbarium%%' THEN 9
        WHEN {u} LIKE '%%specimen%%' THEN 9
        WHEN {u} LIKE '%%jstor%%' THEN 9
        WHEN {u} LIKE '%%scan%%' THEN 9
        WHEN {u} LIKE '%%sheet%%' THEN 9
        ELSE 1
    END
    """


def column_expr(cols: set[str], preferred: list[str]) -> str:
    existing = [c for c in preferred if c in cols]
    if not existing:
        return "''"
    return "COALESCE(" + ", ".join([f"NULLIF({c}, '')" for c in existing]) + ", '')"


def first_existing(cols: set[str], preferred: list[str]) -> str | None:
    for c in preferred:
        if c in cols:
            return c
    return None


def build_region_filter(cols: set[str], scope: str, value: str) -> tuple[str, list[Any], str]:
    """Return SQL WHERE fragment, params, and strategy.

    Important: region-specific calls must never fall back to random global orchids.
    If a user accidentally leaves scope='country' while typing a state such as California,
    this first tries country and then safely tries region/state/locality columns.
    """
    normalized_scope = (scope or "").strip().lower()
    value = (value or "").strip()
    if not value:
        return "FALSE", [], "empty_value"

    country_cols = [c for c in ["country", "country_name"] if c in cols]
    continent_cols = [c for c in ["continent", "continent_name"] if c in cols]
    region_cols = [c for c in [
        "region", "region_name", "state", "state_name", "state_province", "province",
        "county", "locality", "location", "place", "verbatim_locality"
    ] if c in cols]
    state_cols = [c for c in ["state", "state_name", "state_province", "province", "region", "region_name", "locality", "location", "place"] if c in cols]
    county_cols = [c for c in ["county", "county_name", "locality", "location", "place", "verbatim_locality"] if c in cols]
    island_cols = [c for c in ["island", "island_name", "locality", "location", "place", "verbatim_locality"] if c in cols]

    def exact_or_contains(columns: list[str]) -> tuple[str, list[Any]]:
        pieces: list[str] = []
        params: list[Any] = []
        for col in columns:
            pieces.append(f"lower(coalesce({col}, '')) = lower(%s)")
            params.append(value)
            pieces.append(f"lower(coalesce({col}, '')) LIKE lower(%s)")
            params.append(f"%{value}%")
        return " OR ".join(pieces), params

    candidate_cols: list[str] = []
    strategy = normalized_scope
    if normalized_scope == "country":
        candidate_cols = country_cols + region_cols
        strategy = "country_then_region_fields_no_random_fallback"
    elif normalized_scope == "continent":
        candidate_cols = continent_cols
        strategy = "continent_fields_no_random_fallback"
    elif normalized_scope == "island":
        candidate_cols = island_cols
        strategy = "island_fields_no_random_fallback"
    elif normalized_scope in {"state", "province"}:
        candidate_cols = state_cols + country_cols
        strategy = "state_province_fields_no_random_fallback"
    elif normalized_scope == "county":
        candidate_cols = county_cols + state_cols + country_cols
        strategy = "county_fields_no_random_fallback"
    else:
        candidate_cols = region_cols + country_cols
        strategy = "region_fields_no_random_fallback"

    if not candidate_cols:
        return "FALSE", [], "no_matching_columns"
    sql, params = exact_or_contains(candidate_cols)
    return f"({sql})", params, strategy


def best_harvested_image_for_region(conn, value: str | None, scope: str = "region") -> str | None:
    if not value or not table_exists(conn, "public", "images"):
        return None
    cols = fetch_columns(conn, "public", "images")
    filter_sql, params, _strategy = build_region_filter(cols, scope, value)
    if filter_sql == "FALSE":
        return None
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT url
            FROM public.images
            WHERE url IS NOT NULL
              AND {filter_sql}
            ORDER BY {image_score_sql('url')}, random()
            LIMIT 1
            """,
            tuple(params),
        )
        row = cur.fetchone()
        return row["url"] if row else None


def harvested_cards(conn, limit: int, value: str | None = None, scope: str = "country", randomize: bool = False) -> tuple[list[dict[str, Any]], str]:
    if not table_exists(conn, "public", "images"):
        return [], "missing_images_table"

    cols = fetch_columns(conn, "public", "images")
    where = "url IS NOT NULL AND scientific_name IS NOT NULL"
    params: list[Any] = []
    strategy = "global_gallery"

    if value:
        filter_sql, filter_params, strategy = build_region_filter(cols, scope, value)
        where += f" AND {filter_sql}"
        params.extend(filter_params)

    order_clause = "random()" if randomize else "scientific_name, id"
    matched_expr = column_expr(cols, [
        "country", "country_name", "state_province", "state", "province", "region", "region_name",
        "county", "locality", "location", "island", "continent"
    ])
    source_expr = "source" if "source" in cols else "'public.images'"
    id_expr = "id" if "id" in cols else "row_number() OVER ()"

    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {id_expr} AS id,
                   scientific_name,
                   COALESCE(NULLIF(scientific_name, ''), 'Unknown orchid') AS display_name,
                   url AS hero_image_url,
                   {matched_expr} AS matched_value,
                   {source_expr} AS source
            FROM public.images
            WHERE {where}
            ORDER BY {order_clause}
            LIMIT %s
            """,
            tuple(params),
        )
        return [dict(r) for r in cur.fetchall()], strategy


FEATURED_GENERA = ["Vanilla", "Dracula", "Masdevallia", "Cattleya", "Dendrobium", "Bulbophyllum", "Catasetum"]


def genus_for_today() -> str:
    # Same weekday mapping the Famous AI homepage used.
    return FEATURED_GENERA[__import__("datetime").datetime.utcnow().weekday() % len(FEATURED_GENERA)]


def normalize_image_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        name = row.get("scientific_name") or row.get("canonical_name") or row.get("display_name") or "Unknown orchid"
        image = row.get("image_url") or row.get("url") or row.get("hero_image_url") or row.get("photo_url")
        if not image:
            continue
        key = (str(name), str(image))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "scientific_name": name,
            "canonical_name": name,
            "species": name,
            "image_url": image,
            "image_urls": [image],
            "photo_url": image,
            "url": image,
            "medium_url": image,
            "image_source": row.get("image_source") or row.get("source") or row.get("provider") or "Orchid Continuum Brain",
            "image_license": row.get("image_license") or row.get("license"),
            "country": row.get("country"),
            "region": row.get("region") or row.get("state_province") or row.get("matched_value"),
            "photographer": row.get("photographer") or row.get("credit"),
        })
    return out


def genus_image_rows_from_harvested(conn, genus: str, limit: int) -> list[dict[str, Any]]:
    if not table_exists(conn, "public", "images"):
        return []
    cols = fetch_columns(conn, "public", "images")
    source_expr = "source" if "source" in cols else "'public.images'"
    country_expr = first_existing(cols, ["country", "country_name"])
    region_expr = first_existing(cols, ["state_province", "state", "province", "region", "region_name", "locality", "location", "place"])
    photographer_expr = first_existing(cols, ["photographer", "observer", "credit", "user_login"])
    license_expr = first_existing(cols, ["license", "image_license"])
    genus_clause = "lower(split_part(scientific_name, ' ', 1)) = lower(%s)"
    params: list[Any] = [genus]
    if "genus" in cols:
        genus_clause = "(lower(genus) = lower(%s) OR lower(split_part(scientific_name, ' ', 1)) = lower(%s))"
        params = [genus, genus]
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT scientific_name,
                   url AS image_url,
                   {source_expr} AS image_source,
                   {country_expr or 'NULL'} AS country,
                   {region_expr or 'NULL'} AS region,
                   {photographer_expr or 'NULL'} AS photographer,
                   {license_expr or 'NULL'} AS image_license
            FROM public.images
            WHERE scientific_name IS NOT NULL
              AND url IS NOT NULL
              AND {genus_clause}
            ORDER BY scientific_name, {image_score_sql('url')}, random()
            LIMIT %s
            """,
            tuple(params),
        )
        return [dict(r) for r in cur.fetchall()]


def genus_image_rows_from_curated(conn, genus: str, limit: int) -> list[dict[str, Any]]:
    """Best-effort reader for the 5M-row curated image table.

    The current Brain was assembled from several historical schemas, so this is
    intentionally defensive. It only uses the curated table when the expected
    image URL and taxonomy linkage columns are present; otherwise the harvested
    image table remains the safe compatibility source.
    """
    if not (table_exists(conn, "public", "orchid_images") and table_exists(conn, "public", "orchid_taxonomy")):
        return []
    img_cols = fetch_columns(conn, "public", "orchid_images")
    tax_cols = fetch_columns(conn, "public", "orchid_taxonomy")
    image_col = first_existing(img_cols, ["image_url", "url", "photo_url", "media_url", "thumbnail_url"])
    taxonomy_fk = first_existing(img_cols, ["taxonomy_id", "taxon_id", "orchid_taxonomy_id"])
    tax_id_col = first_existing(tax_cols, ["id", "taxonomy_id", "taxon_id"])
    tax_name_col = first_existing(tax_cols, ["scientific_name", "canonical_name", "accepted_name", "name"])
    tax_genus_col = first_existing(tax_cols, ["genus", "genus_name"])
    if not (image_col and taxonomy_fk and tax_id_col and tax_name_col):
        return []
    source_col = first_existing(img_cols, ["image_source", "source", "provider"])
    license_col = first_existing(img_cols, ["image_license", "license", "rights"])
    credit_col = first_existing(img_cols, ["photographer", "creator", "credit", "attribution"])
    genus_where = f"lower(t.{tax_genus_col}) = lower(%s)" if tax_genus_col else f"lower(split_part(t.{tax_name_col}, ' ', 1)) = lower(%s)"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT t.{tax_name_col} AS scientific_name,
                   i.{image_col} AS image_url,
                   {('i.' + source_col) if source_col else "'public.orchid_images'"} AS image_source,
                   {('i.' + license_col) if license_col else 'NULL'} AS image_license,
                   {('i.' + credit_col) if credit_col else 'NULL'} AS photographer,
                   NULL AS country,
                   NULL AS region
            FROM public.orchid_images i
            JOIN public.orchid_taxonomy t ON i.{taxonomy_fk} = t.{tax_id_col}
            WHERE i.{image_col} IS NOT NULL
              AND t.{tax_name_col} IS NOT NULL
              AND {genus_where}
            ORDER BY t.{tax_name_col}, {image_score_sql('i.' + image_col)}, random()
            LIMIT %s
            """,
            (genus, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_genus_images(conn, genus: str, limit: int) -> list[dict[str, Any]]:
    rows = genus_image_rows_from_curated(conn, genus, limit)
    if len(rows) < limit:
        rows.extend(genus_image_rows_from_harvested(conn, genus, limit - len(rows)))
    return normalize_image_rows(rows)[:limit]


def species_counts_for_genus(conn, genus: str) -> dict[str, Any]:
    result = {"species_count": 0, "image_count": 0, "occurrence_count": 0}
    if table_exists(conn, "public", "orchid_taxonomy"):
        tax_cols = fetch_columns(conn, "public", "orchid_taxonomy")
        genus_col = first_existing(tax_cols, ["genus", "genus_name"])
        name_col = first_existing(tax_cols, ["scientific_name", "canonical_name", "accepted_name", "name"])
        if name_col:
            where = f"lower({genus_col}) = lower(%s)" if genus_col else f"lower(split_part({name_col}, ' ', 1)) = lower(%s)"
            result["species_count"] = scalar(conn, f"SELECT COUNT(DISTINCT {name_col}) FROM public.orchid_taxonomy WHERE {where}", (genus,)) or 0
    if table_exists(conn, "public", "images"):
        result["image_count"] = scalar(conn, "SELECT COUNT(*) FROM public.images WHERE scientific_name IS NOT NULL AND lower(split_part(scientific_name, ' ', 1)) = lower(%s)", (genus,)) or 0
    if table_exists(conn, "public", "orchid_occurrence"):
        occ_cols = fetch_columns(conn, "public", "orchid_occurrence")
        name_col = first_existing(occ_cols, ["scientific_name", "canonical_name", "name"])
        genus_col = first_existing(occ_cols, ["genus", "genus_name"])
        if name_col or genus_col:
            where = f"lower({genus_col}) = lower(%s)" if genus_col else f"lower(split_part({name_col}, ' ', 1)) = lower(%s)"
            result["occurrence_count"] = scalar(conn, f"SELECT COUNT(*) FROM public.orchid_occurrence WHERE {where}", (genus,)) or 0
    return result


@app.get("/")
def root():
    return {"service": APP_TITLE, "version": APP_VERSION, "status": "running"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/db/ping")
def db_ping():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database()::text AS database_name, current_schema()::text AS schema_name, current_user::text AS db_user")
                row = cur.fetchone()
        return {"ok": True, "database": row["database_name"], "schema": row["schema_name"], "db_user": row["db_user"]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database ping failed: {exc}")


@app.get("/api/brain/status")
def brain_status():
    """Safe SQL wrapper for Jeff: database counts without needing to run SQL manually."""
    try:
        with get_conn() as conn:
            images_count = scalar(conn, "SELECT COUNT(*) FROM public.images") if table_exists(conn, "public", "images") else 0
            image_taxa = scalar(conn, "SELECT COUNT(DISTINCT scientific_name) FROM public.images WHERE scientific_name IS NOT NULL") if table_exists(conn, "public", "images") else 0
            orchid_images_count = scalar(conn, "SELECT COUNT(*) FROM public.orchid_images") if table_exists(conn, "public", "orchid_images") else 0
            taxonomy_count = scalar(conn, "SELECT COUNT(*) FROM public.orchid_taxonomy") if table_exists(conn, "public", "orchid_taxonomy") else 0
            occurrence_count = scalar(conn, "SELECT COUNT(*) FROM public.orchid_occurrence") if table_exists(conn, "public", "orchid_occurrence") else 0
            harvest_state = []
            if table_exists(conn, "public", "harvest_state"):
                with conn.cursor() as cur:
                    cur.execute("SELECT source, last_offset, total_inserted, updated_at FROM public.harvest_state ORDER BY source")
                    harvest_state = [dict(r) for r in cur.fetchall()]
        return {
            "ok": True,
            "brain": "online",
            "counts": {
                "harvested_images": images_count,
                "harvested_distinct_taxa": image_taxa,
                "curated_orchid_images": orchid_images_count,
                "taxonomy": taxonomy_count,
                "occurrences": occurrence_count,
            },
            "harvest_state": harvest_state,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Brain status failed: {exc}")


@app.get("/api/audit/status")
def audit_status():
    return brain_status()


@app.get("/atlas.html")
def serve_atlas_html():
    return FileResponse(find_atlas_html(), media_type="text/html")


@app.get("/api/genus/daily")
def daily_genus():
    genus = genus_for_today()
    try:
        with get_conn() as conn:
            counts = species_counts_for_genus(conn, genus)
            images = get_genus_images(conn, genus, 4)
        return {
            "ok": True,
            "genus": genus,
            "rank": "genus",
            "display_name": genus,
            "source": "Orchid Continuum Brain",
            "counts": counts,
            "images": images,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Daily genus failed: {exc}")


@app.get("/images/genus/{genus}")
def images_by_genus(genus: str, limit: int = Query(default=20, ge=1, le=200)):
    try:
        with get_conn() as conn:
            images = get_genus_images(conn, genus, limit)
        # Return both an array-compatible body and named collections for old/new consumers.
        return {"genus": genus, "count": len(images), "images": images, "photos": images, "results": images, "source": "Orchid Continuum Brain"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Images by genus failed: {exc}")


@app.get("/api/genus/{genus}/photos")
def genus_photos(genus: str, limit: int = Query(default=30, ge=1, le=200)):
    return images_by_genus(genus=genus, limit=limit)


@app.get("/api/genus/{genus}")
def genus_summary(genus: str, limit: int = Query(default=12, ge=1, le=100)):
    try:
        with get_conn() as conn:
            counts = species_counts_for_genus(conn, genus)
            images = get_genus_images(conn, genus, limit)
        return {
            "ok": True,
            "rank": "genus",
            "genus": genus,
            "display_name": genus,
            "counts": counts,
            "images": images,
            "summary": f"{genus} is being assembled into a live Orchid Continuum genus dossier from taxonomy, images, occurrences, literature, traits, and relationship data.",
            "dossier_status": "foundation_ready",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Genus summary failed: {exc}")


@app.get("/api/genus-story/{genus}")
def genus_story(genus: str, limit: int = Query(default=12, ge=1, le=100)):
    """First version of the narrative packet the homepage should eventually consume."""
    try:
        with get_conn() as conn:
            counts = species_counts_for_genus(conn, genus)
            images = get_genus_images(conn, genus, limit)
        return {
            "ok": True,
            "story_type": "genus",
            "genus": genus,
            "featured_taxon": {"rank": "genus", "name": genus},
            "images": images,
            "species_cards": images,
            "counts": counts,
            "habitats": [],
            "pollinators": [],
            "mycorrhizae": [],
            "neighbor_taxa": [],
            "videos": [],
            "literature": [],
            "glossary_terms": [],
            "conservation": [],
            "narrative": f"The {genus} story packet is online. The Brain can now attach images and counts, with traits, pollinators, mycorrhizae, literature, and conservation layers to follow.",
            "status": "foundation_ready",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Genus story failed: {exc}")


@app.get("/api/species/metrics")
def species_metrics():
    status = brain_status()
    counts = status.get("counts", {})
    return {
        "species_count": counts.get("taxonomy"),
        "genera_count": None,
        "occurrence_count": counts.get("occurrences"),
        "countries_count": None,
        "image_count": counts.get("curated_orchid_images") or counts.get("harvested_images"),
        "pollinator_records": None,
        "last_updated": None,
    }


@app.get("/api/species/featured")
def featured_species(limit: int = Query(default=12, ge=1, le=100)):
    genus = genus_for_today()
    try:
        with get_conn() as conn:
            images = get_genus_images(conn, genus, limit)
        return [
            {
                "taxonomy_id": f"brain:{i}:{item['scientific_name']}",
                "canonical_name": item["scientific_name"],
                "genus": item["scientific_name"].split(" ")[0],
                "representative_image_url": item["image_url"],
                "region": item.get("region") or item.get("country"),
                "knowledge_label": "Brain-linked",
                "confidence_label": "provisional",
            }
            for i, item in enumerate(images)
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Featured species failed: {exc}")


@app.get("/api/species/search")
def species_search(q: str = Query(...), limit: int = Query(default=20, ge=1, le=100)):
    try:
        with get_conn() as conn:
            rows: list[dict[str, Any]] = []
            if table_exists(conn, "public", "orchid_taxonomy"):
                cols = fetch_columns(conn, "public", "orchid_taxonomy")
                name_col = first_existing(cols, ["scientific_name", "canonical_name", "accepted_name", "name"])
                genus_col = first_existing(cols, ["genus", "genus_name"])
                id_col = first_existing(cols, ["id", "taxonomy_id", "taxon_id"])
                if name_col:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            SELECT {id_col or name_col} AS taxonomy_id,
                                   {name_col} AS canonical_name,
                                   {genus_col or "split_part(" + name_col + ", ' ', 1)"} AS genus
                            FROM public.orchid_taxonomy
                            WHERE lower({name_col}) LIKE lower(%s)
                            ORDER BY {name_col}
                            LIMIT %s
                            """,
                            (f"%{q}%", limit),
                        )
                        rows = [dict(r) for r in cur.fetchall()]
            return rows
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Species search failed: {exc}")


@app.get("/api/species/gaps")
def species_gaps():
    return []


@app.get("/api/species/by-name/{canonical_name}")
def species_by_name(canonical_name: str):
    return species_dossier(canonical_name=canonical_name)


@app.get("/api/species/{taxonomy_id}")
def species_by_id(taxonomy_id: str):
    return species_dossier(canonical_name=taxonomy_id)


@app.get("/api/species-dossier/{canonical_name}")
def species_dossier(canonical_name: str):
    """Foundation species dossier packet. This is the template target for traits and story synthesis."""
    try:
        genus = canonical_name.split(" ")[0] if canonical_name else ""
        with get_conn() as conn:
            images = get_genus_images(conn, genus, 8) if genus else []
            best = next((img for img in images if img["scientific_name"].lower() == canonical_name.lower()), None)
        return {
            "taxonomy_id": canonical_name,
            "canonical_name": canonical_name,
            "genus": genus,
            "hero_image_url": best["image_url"] if best else (images[0]["image_url"] if images else None),
            "representative_image_url": best["image_url"] if best else (images[0]["image_url"] if images else None),
            "images": images,
            "sections": {
                "taxonomy": {"status": "foundation_ready"},
                "range": {"status": "pending_geography_enrichment"},
                "habitat": {"status": "pending_trait_habitat_enrichment"},
                "traits": {"status": "pending_trait_table_connection"},
                "pollinators": {"status": "pending_relationship_table_connection"},
                "mycorrhizae": {"status": "pending_relationship_table_connection"},
                "literature": {"status": "pending_literature_pipeline_connection"},
                "videos": {"status": "pending_media_connection"},
                "conservation": {"status": "pending_conservation_layer_connection"},
            },
            "story_summary": "This species dossier shell is ready for Brain-populated facts and AI synthesis from cited evidence.",
            "confidence": {"label": "provisional"},
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Species dossier failed: {exc}")


@app.get("/api/orchid-widgets/region")
def region_legacy(scope: str = Query(...), value: str = Query(...)):
    return region_profile(value=value, scope=scope)


@app.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(limit: int = Query(default=12, ge=1, le=200), randomize: bool = Query(default=False)):
    try:
        with get_conn() as conn:
            cards, strategy = harvested_cards(conn, limit=limit, randomize=randomize)
        return {"widget": "featured_gallery", "source": "public.images", "count": len(cards), "match_strategy": strategy, "cards": cards}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Featured gallery failed: {exc}")


@app.get("/api/orchid-widgets/region-profile")
def region_profile(value: str = Query(..., description="region slug, alias, or region name"), scope: str = Query(default="region")):
    try:
        with get_conn() as conn:
            region = None
            habitats = []
            media = []
            if table_exists(conn, "oc_regions", "region_profiles"):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        WITH target AS (
                            SELECT rp.* FROM oc_regions.region_profiles rp
                            WHERE lower(rp.region_slug) = lower(%s) OR lower(rp.region_name) = lower(%s)
                            UNION ALL
                            SELECT rp.* FROM oc_regions.region_aliases ra
                            JOIN oc_regions.region_profiles rp ON rp.region_slug = ra.region_slug
                            WHERE lower(ra.alias) = lower(%s)
                            LIMIT 1
                        ) SELECT * FROM target
                        """,
                        (value, value, value),
                    )
                    region = cur.fetchone()
                    if region:
                        if table_exists(conn, "oc_regions", "region_habitats"):
                            cur.execute("SELECT habitat_name, habitat_description, image_url, image_caption, sort_order FROM oc_regions.region_habitats WHERE region_slug=%s ORDER BY sort_order", (region["region_slug"],))
                            habitats = cur.fetchall()
                        if table_exists(conn, "oc_regions", "region_media"):
                            cur.execute("SELECT media_type, media_url, caption, credit, sort_order FROM oc_regions.region_media WHERE region_slug=%s ORDER BY sort_order", (region["region_slug"],))
                            media = cur.fetchall()

            fallback_image = best_harvested_image_for_region(conn, value, scope=scope)

        if region:
            r = dict(region)
        else:
            r = {
                "region_slug": value.strip().lower().replace(" ", "-"),
                "region_name": value,
                "scope": scope,
                "country_name": value if scope == "country" else None,
                "continent_name": value if scope == "continent" else None,
                "short_description": f"Live Orchid Continuum region profile for {value} using harvested orchid records.",
                "orchid_significance": "This profile is backed by the active Orchid Continuum harvester and will improve as the Brain fills in taxonomy, geography, images, and literature.",
                "habitat_summary": "Habitat summaries are being assembled from curated regional records and harvested observations.",
                "climate_summary": "Climate intelligence is pending enrichment.",
                "elevation_summary": "Elevation intelligence is pending enrichment.",
                "conservation_summary": "Conservation intelligence is pending enrichment.",
                "is_featured": False,
            }

        r["hero_image_url"] = r.get("hero_image_url") or fallback_image
        r["habitats"] = [dict(h) for h in habitats]
        r["media"] = [dict(m) for m in media]
        return {"widget": "region_profile", "region": r}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Region profile failed: {exc}")


@app.get("/api/orchid-widgets/orchids-by-region")
def orchids_by_region(scope: str = Query(...), value: str = Query(...), limit: int = Query(default=24, ge=1, le=200)):
    try:
        normalized_scope = scope.strip().lower()
        if normalized_scope not in {"country", "region", "island", "continent", "state", "province", "county"}:
            raise HTTPException(status_code=400, detail="scope must be one of: country, state, province, county, region, island, continent")
        with get_conn() as conn:
            orchids, strategy = harvested_cards(conn, limit=limit, value=value, scope=normalized_scope, randomize=False)
        response = {
            "widget": "orchids_by_region",
            "scope": normalized_scope,
            "value": value,
            "source": "public.images",
            "count": len(orchids),
            "match_strategy": strategy,
            "orchids": orchids,
        }
        if not orchids:
            response["mapping_note"] = f"No harvested orchid images matched {normalized_scope}={value}. No unrelated global fallback was used."
        return response
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Orchids by region failed: {exc}")


@app.get("/api/orchid-widgets/region-intelligence")
def region_intelligence(scope: str = Query(...), value: str = Query(...)):
    try:
        normalized_scope = scope.strip().lower()
        with get_conn() as conn:
            row = None
            if table_exists(conn, "oc_intelligence", "v_region_species_summary"):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT region_scope, region_name, parent_region_name, occurrence_count, species_count,
                               taxonomy_count, genus_count, endemic_proxy_species_count, min_elevation_m,
                               max_elevation_m, avg_elevation_m, georeferenced_occurrence_count,
                               dominant_climate_preference, dominant_growth_habit, top_genera, created_at
                        FROM oc_intelligence.v_region_species_summary
                        WHERE lower(region_scope)=lower(%s) AND lower(region_name)=lower(%s)
                        LIMIT 1
                        """,
                        (normalized_scope, value),
                    )
                    row = cur.fetchone()
            if not row and table_exists(conn, "public", "images"):
                cols = fetch_columns(conn, "public", "images")
                filter_sql, params, _strategy = build_region_filter(cols, normalized_scope, value)
                genus_expr = "genus" if "genus" in cols else "split_part(scientific_name, ' ', 1)"
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT COUNT(*)::int AS occurrence_count,
                               COUNT(DISTINCT scientific_name)::int AS species_count,
                               COUNT(DISTINCT {genus_expr})::int AS genus_count
                        FROM public.images
                        WHERE scientific_name IS NOT NULL AND {filter_sql}
                        """,
                        tuple(params),
                    )
                    counts = cur.fetchone()
                row = {
                    "region_scope": normalized_scope,
                    "region_name": value,
                    "parent_region_name": None,
                    "occurrence_count": counts["occurrence_count"],
                    "species_count": counts["species_count"],
                    "taxonomy_count": counts["species_count"],
                    "genus_count": counts["genus_count"],
                    "endemic_proxy_species_count": None,
                    "min_elevation_m": None,
                    "max_elevation_m": None,
                    "avg_elevation_m": None,
                    "georeferenced_occurrence_count": None,
                    "dominant_climate_preference": None,
                    "dominant_growth_habit": None,
                    "top_genera": [],
                    "created_at": None,
                }
        return {"widget": "region_intelligence", "scope": normalized_scope, "value": value, "summary": row}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Region intelligence failed: {exc}")


@app.get("/api/orchid-widgets/top-regions")
def top_regions(scope: str = Query(...), sort_by: str = Query(default="species_count"), limit: int = Query(default=10, ge=1, le=100)):
    try:
        normalized_scope = scope.strip().lower()
        with get_conn() as conn:
            rows = []
            if table_exists(conn, "oc_intelligence", "v_region_species_summary"):
                order_clause = "species_count DESC, occurrence_count DESC, region_name" if sort_by == "species_count" else "occurrence_count DESC, species_count DESC, region_name"
                with conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM oc_intelligence.v_region_species_summary WHERE lower(region_scope)=lower(%s) ORDER BY {order_clause} LIMIT %s", (normalized_scope, limit))
                    rows = [dict(r) for r in cur.fetchall()]
            elif table_exists(conn, "public", "images"):
                cols = fetch_columns(conn, "public", "images")
                if normalized_scope == "continent" and "continent" in cols:
                    group_col = "continent"
                elif normalized_scope in {"state", "province", "region"} and "state_province" in cols:
                    group_col = "state_province"
                elif normalized_scope in {"state", "province", "region"} and "region" in cols:
                    group_col = "region"
                elif normalized_scope == "county" and "county" in cols:
                    group_col = "county"
                else:
                    group_col = "country" if "country" in cols else None
                if group_col:
                    with conn.cursor() as cur:
                        cur.execute(f"SELECT %s AS region_scope, {group_col} AS region_name, COUNT(*)::int AS occurrence_count, COUNT(DISTINCT scientific_name)::int AS species_count FROM public.images WHERE {group_col} IS NOT NULL AND {group_col} <> '' GROUP BY {group_col} ORDER BY species_count DESC, occurrence_count DESC LIMIT %s", (normalized_scope, limit))
                        rows = [dict(r) for r in cur.fetchall()]
        return {"widget": "top_regions", "scope": normalized_scope, "sort_by": sort_by, "count": len(rows), "rows": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Top regions failed: {exc}")
