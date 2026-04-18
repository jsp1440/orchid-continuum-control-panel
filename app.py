# ================================
# REGION PROFILE ENDPOINT
# ================================

from fastapi import Query, HTTPException

@app.get("/api/orchid-widgets/region-profile")
def region_profile(
    scope: str = Query(..., description="continent | country | region | island"),
    value: str = Query(..., description="region name or slug")
):
    """
    Returns region profile + habitats + media
    """

    sql = """
    WITH target AS (
        SELECT *
        FROM oc_regions.region_profiles
        WHERE
            lower(region_slug) = lower(%s)
            OR lower(region_name) = lower(%s)
        LIMIT 1
    ),
    habitats AS (
        SELECT
            habitat_name,
            habitat_description,
            image_url,
            image_caption,
            sort_order
        FROM oc_regions.region_habitats
        WHERE region_slug = (SELECT region_slug FROM target)
        ORDER BY sort_order
    ),
    media AS (
        SELECT
            media_type,
            media_url,
            caption,
            credit,
            sort_order
        FROM oc_regions.region_media
        WHERE region_slug = (SELECT region_slug FROM target)
        ORDER BY sort_order
    )
    SELECT
        t.region_slug,
        t.region_name,
        t.scope,
        t.continent_name,
        t.country_name,
        t.short_description,
        t.orchid_significance,
        t.habitat_summary,
        t.climate_summary,
        t.elevation_summary,
        t.conservation_summary,
        t.hero_image_url,
        t.hero_image_caption,
        t.video_url,
        COALESCE(
            (SELECT json_agg(h) FROM habitats h),
            '[]'
        ) AS habitats,
        COALESCE(
            (SELECT json_agg(m) FROM media m),
            '[]'
        ) AS media
    FROM target t;
    """

    with psycopg.connect(get_database_url()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (value, value))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Region not found")

    return {
        "widget": "region_profile",
        "region": row
    }
