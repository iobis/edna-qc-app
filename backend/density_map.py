from typing import List, Optional, Tuple
import json
import logging
import math
import os

import antimeridian
import duckdb
import h3

from analysis import SPEEDY_DATA_DIR, SPEEDY_RESOLUTION

logger = logging.getLogger(__name__)

_DEFAULT_SPECIESGRIDS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "speciesgrids",
)
SPECIESGRIDS_DIR = os.environ.get(
    "SPECIESGRIDS_DIR",
    "/data/speciesgrids" if os.path.isdir("/data/speciesgrids") else _DEFAULT_SPECIESGRIDS,
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

    h3_indices = [row[0] for row in rows]
    values = [row[1] for row in rows]

    geometries: List[dict] = []
    for h3_index in h3_indices:
        boundary = h3.cell_to_boundary(h3_index)
        ring = [[lng, lat] for lat, lng in boundary]
        ring.append(ring[0])
        geometries.append({"type": "Polygon", "coordinates": [ring]})

    features: List[dict] = []
    for h3_index, value, geometry in zip(h3_indices, values, geometries):
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
                    "h3": h3_index,
                    value_property: _sanitize(value),
                    "is_occurrence": h3_index == occurrence_h3 if occurrence_h3 else False,
                },
            }
        )

    return features


def _speciesgrids_parquet_paths() -> List[str]:
    if not os.path.isdir(SPECIESGRIDS_DIR):
        raise FileNotFoundError(f"Species grids directory not found: {SPECIESGRIDS_DIR}")

    paths: List[str] = []
    for root, _, files in os.walk(SPECIESGRIDS_DIR):
        for name in files:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                paths.append(path)

    if not paths:
        raise FileNotFoundError(f"No species grid files found in {SPECIESGRIDS_DIR}")

    return sorted(paths)


def get_speciesgrids_records_geojson(aphiaid: int) -> dict:
    parquet_paths = _speciesgrids_parquet_paths()

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
            [parquet_paths, aphiaid],
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
    file_path = os.path.join(SPEEDY_DATA_DIR, f"{aphiaid}.parquet")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"No density map found for AphiaID {aphiaid}")

    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute("INSTALL h3 FROM community;")
        conn.execute("LOAD h3;")

        occurrence_h3 = None
        if lon is not None and lat is not None:
            occurrence_h3 = conn.execute(
                "SELECT h3_latlng_to_cell_string(?, ?, ?)",
                [float(lat), float(lon), SPEEDY_RESOLUTION],
            ).fetchone()[0]

        if occurrence_h3 is not None:
            rows = conn.execute(
                """
                SELECT h3, density
                FROM read_parquet(?)
                WHERE density >= ? OR h3 = ?
                """,
                [file_path, DENSITY_MAP_MIN_DENSITY, occurrence_h3],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT h3, density
                FROM read_parquet(?)
                WHERE density >= ?
                """,
                [file_path, DENSITY_MAP_MIN_DENSITY],
            ).fetchall()
    finally:
        conn.close()

    features = _build_h3_features(rows, "density", occurrence_h3=occurrence_h3)

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
    file_path = os.path.join(SPEEDY_DATA_DIR, f"{aphiaid}.parquet")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"No suitability map found for AphiaID {aphiaid}")

    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute("INSTALL h3 FROM community;")
        conn.execute("LOAD h3;")

        occurrence_h3 = None
        if lon is not None and lat is not None:
            occurrence_h3 = conn.execute(
                "SELECT h3_latlng_to_cell_string(?, ?, ?)",
                [float(lat), float(lon), SPEEDY_RESOLUTION],
            ).fetchone()[0]

        if occurrence_h3 is not None:
            rows = conn.execute(
                """
                SELECT h3, suitability
                FROM read_parquet(?)
                WHERE suitability >= ? OR h3 = ?
                """,
                [file_path, DENSITY_MAP_MIN_SUITABILITY, occurrence_h3],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT h3, suitability
                FROM read_parquet(?)
                WHERE suitability >= ?
                """,
                [file_path, DENSITY_MAP_MIN_SUITABILITY],
            ).fetchall()
    finally:
        conn.close()

    features = _build_h3_features(rows, "suitability", occurrence_h3=occurrence_h3)

    return {
        "type": "FeatureCollection",
        "features": features,
        "occurrence": {"lon": lon, "lat": lat} if lon is not None and lat is not None else None,
    }
