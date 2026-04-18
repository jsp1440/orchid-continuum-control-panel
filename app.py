import os
from decimal import Decimal
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

APP_TITLE = "Orchid Continuum Control Panel API"
APP_VERSION = "0.1.0"


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
    return {"ok": True}


@app.get("/db/ping")
def db_ping() -> dict[str, Any]:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_database()::text AS database,
                        current_schema()::text AS schema_name,
                        current_user::text AS db_user
                    """
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("Database ping returned no row")

                return {
                    "ok": True,
                    "database": row["database"],
                    "schema": row["schema_name"],
                    "db_user": row["db_user"],
                }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database ping failed: {exc}") from exc


@app.get("/api/orchid-widgets/featured-gallery")
def featured_gallery(limit: int = Query(default=3, ge=1, le=48)) -> dict[str, Any]:
    """
    Schema-audited version using verified fields only.

    Verified from your screenshots:
    orchid_images:
      - taxonomy_id
      - image_url

    orchid_taxonomy:
      - id
      - scientific_name
      - canonical_name
      - full_scientific_name
      - genus
      - family_name
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        t.id,
                        COALESCE(
                            NULLIF(t.canonical_name, ''),
                            NULLIF(t.scientific_name, ''),
                            NULLIF(t.full_scientific_name, '')
                        ) AS scientific_name,
                        t.genus,
                        t.family_name AS family,
                        i.image_url
                    FROM orchid_images i
                    JOIN orchid_taxonomy t
                      ON i.taxonomy_id = t.id
                    WHERE i.image_url IS NOT NULL
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()

        cards = [
            {
                "id": row["id"],
                "scientific_name": row["scientific_name"],
                "display_name": row["scientific_name"],
                "genus": row["genus"],
                "family": row["family"],
                "hero_image_url": row["image_url"],
            }
            for row in rows
        ]

        return {
            "widget": "featured_gallery",
            "count": len(cards),
            "cards": cards,
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
