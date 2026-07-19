from typing import List, Optional, Tuple
import json
import logging
import math
import os
import threading

import antimeridian
import h3

from grid_products import (
    DENSITY_PATH,
    density_rows_for_aphiaid,
    h3_to_str,
    latlng_to_h3_int,
    open_h3_connection,
    open_spatial_connection,
    suitability_rows_for_aphiaid,
)
from map_cache import apply_occurrence_flag, get_cached_map, store_cached_map

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

_h3_geometry_cache: dict = {}
_h3_geometry_lock = threading.Lock()
_records_memory_cache: dict = {}
_records_memory_lock = threading.Lock()
_RECORDS_MEMORY_MAX = 64


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


def _h3_polygon_geometry(h3_str: str) -> Optional[dict]:
    with _h3_geometry_lock:
        cached = _h3_geometry_cache.get(h3_str)
    if cached is not None:
        return cached

    boundary = h3.cell_to_boundary(h3_str)
    ring = [[lng, lat] for lat, lng in boundary]
    ring.append(ring[0])
    geometry = {"type": "Polygon", "coordinates": [ring]}

    if not _geometry_is_valid(geometry):
        fixed = antimeridian.fix_geojson(geometry)
        if not _geometry_is_valid(fixed):
            return None
        geometry = fixed

    with _h3_geometry_lock:
        _h3_geometry_cache[h3_str] = geometry
    return geometry


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
        geometry = _h3_polygon_geometry(h3_str)
        if geometry is None:
            continue

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


def _occurrence_h3_str(lon: Optional[float], lat: Optional[float]) -> Optional[str]:
    if lon is None or lat is None:
        return None
    return h3_to_str(latlng_to_h3_int(float(lat), float(lon)))


def _ensure_occurrence_feature(
    features: List[dict],
    value_property: str,
    occurrence_h3: Optional[str],
    occurrence_value: Optional[float],
) -> List[dict]:
    if not occurrence_h3:
        return features
    for feature in features:
        if feature.get("properties", {}).get("h3") == occurrence_h3:
            return features
    if occurrence_value is None:
        return features
    geometry = _h3_polygon_geometry(occurrence_h3)
    if geometry is None:
        return features
    return features + [
        {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "h3": occurrence_h3,
                value_property: _sanitize(occurrence_value),
                "is_occurrence": True,
            },
        }
    ]


def get_speciesgrids_records_geojson(aphiaid: int) -> dict:
    aid = int(aphiaid)
    with _records_memory_lock:
        cached = _records_memory_cache.get(aid)
    if cached is not None:
        return cached

    parquet_paths = _speciesgrids_parquet_paths()
    logger.info(
        "Loading speciesgrids records for AphiaID %s from %d parquet file(s)",
        aphiaid,
        len(parquet_paths),
    )

    conn = open_spatial_connection()
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
        [parquet_paths if len(parquet_paths) > 1 else parquet_paths[0], aid],
    ).fetchall()

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

    payload = {"type": "FeatureCollection", "features": features}
    with _records_memory_lock:
        _records_memory_cache[aid] = payload
        while len(_records_memory_cache) > _RECORDS_MEMORY_MAX:
            oldest = next(iter(_records_memory_cache))
            if oldest == aid:
                break
            del _records_memory_cache[oldest]
    return payload


def get_density_geojson(
    aphiaid: int,
    lon: Optional[float] = None,
    lat: Optional[float] = None,
) -> dict:
    aid = int(aphiaid)
    occurrence_h3_str = _occurrence_h3_str(lon, lat)

    cached = get_cached_map("density", aid, DENSITY_MAP_MIN_DENSITY)
    if cached is None:
        conn = open_h3_connection()
        rows = density_rows_for_aphiaid(
            conn,
            aid,
            DENSITY_MAP_MIN_DENSITY,
            occurrence_h3=None,
        )
        if not rows:
            # Distinguish "species absent" from "all cells below threshold"
            probe = conn.execute(
                "SELECT 1 FROM read_parquet(?) WHERE AphiaID = ? LIMIT 1",
                [DENSITY_PATH, aid],
            ).fetchone()
            if probe is None:
                raise FileNotFoundError(
                    f"No density map found for AphiaID {aphiaid} in {DENSITY_PATH}"
                )

        features = _build_h3_features(rows, "density", occurrence_h3=None)
        cached = {"type": "FeatureCollection", "features": features}
        store_cached_map("density", aid, DENSITY_MAP_MIN_DENSITY, cached)
        logger.info(
            "Built density map cache for AphiaID %s (%s features)",
            aid,
            len(features),
        )

    payload = apply_occurrence_flag(cached, occurrence_h3_str)

    # Include occurrence cell even when below the display threshold.
    if occurrence_h3_str:
        present = {
            f["properties"]["h3"] for f in payload["features"] if f.get("properties")
        }
        if occurrence_h3_str not in present:
            conn = open_h3_connection()
            occurrence_h3_int = latlng_to_h3_int(float(lat), float(lon))
            cell_rows = density_rows_for_aphiaid(
                conn,
                aid,
                0.0,
                occurrence_h3=occurrence_h3_int,
            )
            value = next(
                (
                    dens
                    for h3_cell, dens in cell_rows
                    if h3_to_str(h3_cell) == occurrence_h3_str
                ),
                0.0,
            )
            payload["features"] = _ensure_occurrence_feature(
                payload["features"],
                "density",
                occurrence_h3_str,
                value,
            )

    return {
        **payload,
        "occurrence": {"lon": lon, "lat": lat} if lon is not None and lat is not None else None,
    }


def get_suitability_geojson(
    aphiaid: int,
    lon: Optional[float] = None,
    lat: Optional[float] = None,
) -> dict:
    aid = int(aphiaid)
    occurrence_h3_str = _occurrence_h3_str(lon, lat)

    cached = get_cached_map("suitability", aid, DENSITY_MAP_MIN_SUITABILITY)
    if cached is None:
        try:
            all_rows = suitability_rows_for_aphiaid(aid)
        except FileNotFoundError as e:
            raise FileNotFoundError(f"No suitability map found for AphiaID {aphiaid}") from e

        rows = [
            (h3_cell, value)
            for h3_cell, value in all_rows
            if value >= DENSITY_MAP_MIN_SUITABILITY
        ]
        features = _build_h3_features(rows, "suitability", occurrence_h3=None)
        cached = {"type": "FeatureCollection", "features": features}
        store_cached_map("suitability", aid, DENSITY_MAP_MIN_SUITABILITY, cached)
        logger.info(
            "Built suitability map cache for AphiaID %s (%s features)",
            aid,
            len(features),
        )

    payload = apply_occurrence_flag(cached, occurrence_h3_str)

    if occurrence_h3_str:
        present = {
            f["properties"]["h3"] for f in payload["features"] if f.get("properties")
        }
        if occurrence_h3_str not in present:
            try:
                all_rows = suitability_rows_for_aphiaid(aid)
            except FileNotFoundError:
                all_rows = []
            value_by_h3 = {h3_to_str(h3_cell): value for h3_cell, value in all_rows}
            payload["features"] = _ensure_occurrence_feature(
                payload["features"],
                "suitability",
                occurrence_h3_str,
                value_by_h3.get(occurrence_h3_str, 0.0),
            )

    return {
        **payload,
        "occurrence": {"lon": lon, "lat": lat} if lon is not None and lat is not None else None,
    }
