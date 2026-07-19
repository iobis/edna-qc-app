from typing import List, Optional, Tuple
import json
import logging
import math
import os

import antimeridian
import duckdb
import h3

from grid_products import (
    DENSITY_PATH,
    density_rows_for_aphiaid,
    has_density_for_aphiaid,
    h3_to_str,
    latlng_to_h3,
    open_h3_connection,
    suitability_rows_for_aphiaid,
)

logger = logging.getLogger(__name__)

_DEFAULT_SPECIESGRIDS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "speciesgrids",
)
SPECIESGRIDS_PATH = os.environ.get("SPECIESGRIDS_PATH")
SPECIESGRIDS_DIR = os.environ.get(
    "SPECIESGRIDS_DIR",
    "/data/speciesgrids" if os.path.exists("/data/speciesgrids") else _DEFAULT_SPECIESGRIDS,
)
DENSITY_MAP_MIN_DENSITY = float(os.environ.get("DENSITY_MAP_MIN_DENSITY", "0.08"))
DENSITY_MAP_MIN_SUITABILITY = float(os.environ.get("DENSITY_MAP_MIN_SUITABILITY", "0.08"))


def _sanitize(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _ring_longitude_span(ring) -> float:
    lons = [point[0] for point in ring]
    return max(lons) - min(lons)


def _geometry_is_valid(geometry: dict) -> bool:
    if geometry["type"] == "Polygon":
        rings = geometry["coordinates"]
    elif geometry["type"] == "MultiPolygon":
        rings = [polygon[0] for polygon in geometry["coordinates"]]
    else:
        return False

    return all(_ring_longitude_span(ring) <= 180 for ring in rings)


def _build_h3_features(
    rows: List[Tuple],
    value_property: str,
    occurrence_h3: Optional[str] = None,
) -> List[dict]:
    if not rows:
        return []

    features: List[dict] = []
    for h3_index, value in rows:
        h3_str = h3_to_str(h3_index) if not isinstance(h3_index, str) else h3_index
        boundary = h3.cell_to_boundary(h3_str)
        ring = [[lng, lat] for lat, lng in boundary]
        ring.append(ring[0])
        geometry = {"type": "Polygon", "coordinates": [ring]}

        if not _geometry_is_valid(geometry):
            fixed = antimeridian.fix_geojson(geometry)
            if not _geometry_is_valid(fixed):
                continue
            geometry = fixed

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "h3": h3_str,
                    value_property: _sanitize(value),
                    "is_occurrence": h3_str == occurrence_h3 if occurrence_h3 else False,
                },
            }
        )

    return features


def _speciesgrids_parquet_paths() -> List[str]:
    """
    Resolve speciesgrids parquet input.

    Supports:
    - SPECIESGRIDS_PATH pointing at a single .parquet file
    - SPECIESGRIDS_DIR / SPECIESGRIDS_PATH pointing at a directory of parquet files
    """
    candidates = [SPECIESGRIDS_PATH, SPECIESGRIDS_DIR]
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isfile(candidate) and candidate.lower().endswith(".parquet"):
            return [candidate]
        if os.path.isdir(candidate):
            paths: List[str] = []
            for root, _, files in os.walk(candidate):
                for name in files:
                    path = os.path.join(root, name)
                    if os.path.isfile(path) and name.lower().endswith(".parquet"):
                        paths.append(path)
            if paths:
                return sorted(paths)

    raise FileNotFoundError(
        "No speciesgrids parquet found. Set SPECIESGRIDS_PATH to a .parquet file "
        f"or SPECIESGRIDS_DIR to a directory (tried PATH={SPECIESGRIDS_PATH!r}, "
        f"DIR={SPECIESGRIDS_DIR!r})."
    )


def get_speciesgrids_records_geojson(aphiaid: int) -> dict:
    parquet_paths = _speciesgrids_parquet_paths()
    logger.info(
        "Loading speciesgrids records for AphiaID %s from %d parquet file(s)",
        aphiaid,
        len(parquet_paths),
    )

    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute("INSTALL spatial;")
        conn.execute("LOAD spatial;")

        rows = conn.execute(
            """
            SELECT
                cell,
                records,
                min_year,
                max_year,
                ST_AsGeoJSON(geometry) AS geometry_json
            FROM read_parquet(?)
            WHERE AphiaID = ?
            """,
            [parquet_paths if len(parquet_paths) > 1 else parquet_paths[0], aphiaid],
        ).fetchall()
    finally:
        conn.close()

    features = []
    for cell, records, min_year, max_year, geometry_json in rows:
        features.append(
            {
                "type": "Feature",
                "geometry": json.loads(geometry_json),
                "properties": {
                    "cell": cell,
                    "records": int(records),
                    "min_year": _sanitize(min_year),
                    "max_year": _sanitize(max_year),
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


def get_density_geojson(
    aphiaid: int,
    lon: Optional[float] = None,
    lat: Optional[float] = None,
) -> dict:
    conn = open_h3_connection()
    try:
        if not has_density_for_aphiaid(conn, aphiaid):
            raise FileNotFoundError(
                f"No density map found for AphiaID {aphiaid} in {DENSITY_PATH}"
            )

        occurrence_h3_int = None
        occurrence_h3_str = None
        if lon is not None and lat is not None:
            occurrence_h3_int = latlng_to_h3(conn, float(lat), float(lon))
            occurrence_h3_str = h3_to_str(occurrence_h3_int)

        rows = density_rows_for_aphiaid(
            conn,
            aphiaid,
            DENSITY_MAP_MIN_DENSITY,
            occurrence_h3=occurrence_h3_int,
        )
    finally:
        conn.close()

    features = _build_h3_features(rows, "density", occurrence_h3=occurrence_h3_str)

    return {
        "type": "FeatureCollection",
        "features": features,
        "occurrence": {"lon": lon, "lat": lat} if lon is not None and lat is not None else None,
    }


def get_suitability_geojson(
    aphiaid: int,
    lon: Optional[float] = None,
    lat: Optional[float] = None,
) -> dict:
    try:
        all_rows = suitability_rows_for_aphiaid(aphiaid)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"No suitability map found for AphiaID {aphiaid}") from e

    occurrence_h3_str = None
    if lon is not None and lat is not None:
        conn = open_h3_connection()
        try:
            occurrence_h3_str = h3_to_str(latlng_to_h3(conn, float(lat), float(lon)))
        finally:
            conn.close()

    rows = [
        (h3_cell, value)
        for h3_cell, value in all_rows
        if value >= DENSITY_MAP_MIN_SUITABILITY
        or (occurrence_h3_str is not None and h3_to_str(h3_cell) == occurrence_h3_str)
    ]

    features = _build_h3_features(rows, "suitability", occurrence_h3=occurrence_h3_str)

    return {
        "type": "FeatureCollection",
        "features": features,
        "occurrence": {"lon": lon, "lat": lat} if lon is not None and lat is not None else None,
    }
