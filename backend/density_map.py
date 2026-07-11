from typing import Optional
import copy
import json
import math
import os

import antimeridian
import duckdb
import h3

from analysis import SPEEDY_DATA_DIR, SPEEDY_RESOLUTION

_DEFAULT_SPECIESGRIDS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "speciesgrids",
)
SPECIESGRIDS_DIR = os.environ.get(
    "SPECIESGRIDS_DIR",
    "/data/speciesgrids" if os.path.isdir("/data/speciesgrids") else _DEFAULT_SPECIESGRIDS,
)


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


def _h3_cell_to_geojson_geometry(h3_index: str) -> Optional[dict]:
    geo = copy.deepcopy(h3.cells_to_geo([h3_index]))
    fixed = antimeridian.fix_geojson(geo)
    if not _geometry_is_valid(fixed):
        return None
    return fixed


def _speciesgrids_glob() -> str:
    pattern = os.path.join(SPECIESGRIDS_DIR, "*")
    if not os.path.isdir(SPECIESGRIDS_DIR):
        raise FileNotFoundError(f"Species grids directory not found: {SPECIESGRIDS_DIR}")
    return pattern


def get_speciesgrids_records_geojson(aphiaid: int) -> dict:
    glob_pattern = _speciesgrids_glob()

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
            [glob_pattern, aphiaid],
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


def get_density_map_geojson(
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

        rows = conn.execute(
            """
            SELECT h3, density, suitability
            FROM read_parquet(?)
            """,
            [file_path],
        ).fetchall()
    finally:
        conn.close()

    features = []
    for h3_index, density, suitability, in rows:
        geometry = _h3_cell_to_geojson_geometry(h3_index)
        if geometry is None:
            continue

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "h3": h3_index,
                    "density": _sanitize(density),
                    "suitability": _sanitize(suitability),
                    "is_occurrence": h3_index == occurrence_h3 if occurrence_h3 else False,
                },
            }
        )

    try:
        records = get_speciesgrids_records_geojson(aphiaid)
    except FileNotFoundError:
        records = {"type": "FeatureCollection", "features": []}

    return {
        "type": "FeatureCollection",
        "features": features,
        "occurrence": {"lon": lon, "lat": lat} if lon is not None and lat is not None else None,
        "records": records,
    }
