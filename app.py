import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg
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


@app.get("/api/orchid-atlas")
def orchid_atlas(
    mode: str = Query(
        default="species",
        description="Atlas weighting mode: 'species' or 'records'",
    ),
    min_records: int = Query(
        default=1,
        ge=1,
        description="Minimum records required for a grid cell to be returned",
    ),
    min_species: int = Query(
        default=1,
        ge=1,
        description="Minimum species_count required for a grid cell to be returned",
    ),
    limit: int = Query(
        default=5000,
        ge=1,
        le=100000,
        description="Maximum number of atlas cells to return",
    ),
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
                "lat": float(row["lat_band"]),
                "lon": float(row["lon_band"]),
                "records": int(row["records"]),
                "species_count": int(row["species_count"]),
                "genus_count": int(row["genus_count"]),
                "min_elevation_m": row["min_elevation_m"],
                "max_elevation_m": row["max_elevation_m"],
                "country_count": int(row["country_count"]),
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
            detail=(
                "Atlas layer is missing. Ensure these database objects exist: "
                "public.orchid_atlas_layer."
            ),
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
                    "lat": float(row["lat_band"]),
                    "lon": float(row["lon_band"]),
                    "records": int(row["records"]),
                    "species_count": int(row["species_count"]),
                    "genus_count": int(row["genus_count"]),
                    "min_elevation_m": row["min_elevation_m"],
                    "max_elevation_m": row["max_elevation_m"],
                    "country_count": int(row["country_count"]),
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
                        "coordinates": [float(row["lon_band"]), float(row["lat_band"])],
                    },
                    "properties": {
                        "records": int(row["records"]),
                        "species_count": int(row["species_count"]),
                        "genus_count": int(row["genus_count"]),
                        "min_elevation_m": row["min_elevation_m"],
                        "max_elevation_m": row["max_elevation_m"],
                        "country_count": int(row["country_count"]),
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
