from fastapi import APIRouter
from psycopg.rows import dict_row
import psycopg

router = APIRouter()

@router.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(limit: int = 3):
    try:
        with psycopg.connect(row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        t.id,
                        COALESCE(t.canonical_name, t.scientific_name, t.full_scientific_name) AS name,
                        i.image_url
                    FROM orchid_images i
                    JOIN orchid_taxonomy t
                        ON i.taxonomy_id = t.id
                    WHERE i.image_url IS NOT NULL
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (limit,)
                )

                rows = cur.fetchall()

                return {
                    "count": len(rows),
                    "results": rows
                }

    except Exception as e:
        return {"error": str(e)}
