from typing import List, Optional, Tuple
import hashlib
import json
import logging
import math
import os
import threading
import time

import antimeridian
import h3

from grid_products import (
    DENSITY_PATH,
    DENSITY_SCALE,
    density_path_ok,
    density_rows_for_aphiaid,
    h3_to_str,
    latlng_to_h3_int,
    open_h3_connection,
    open_spatial_connection,
    suitability_rows_for_aphiaid,
)
from map_cache import (
    MAP_CACHE_VERSION,
    apply_occurrence_flag,
    get_cached_json,
    get_cached_map,
    store_cached_json,
    store_cached_map,
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
CONGENER_MIN_DENSITY = float(
    os.environ.get("CONGENER_MIN_DENSITY", str(DENSITY_MAP_MIN_DENSITY))
)
CONGENER_MAX_SPECIES = int(os.environ.get("CONGENER_MAX_SPECIES", "12"))
CONGENER_NEAR_RINGS = int(os.environ.get("CONGENER_NEAR_RINGS", "2"))

# Light, evenly spaced hues. A dark halo in the map keeps them readable.
CONGENER_COLORS = [
    "#5b9bff",
    "#ff7d7d",
    "#5dcc8c",
    "#c084fc",
    "#ffb020",
    "#22d3ee",
    "#fb7185",
    "#c6e056",
    "#fb923c",
    "#a78bfa",
    "#2dd4bf",
    "#f472b6",
]

_GENUS_QUALIFIERS = {
    "cf",
    "aff",
    "sp",
    "spp",
    "nr",
    "var",
    "subsp",
    "indet",
    "species",
    "gen",
    "x",
}

_h3_geometry_cache: dict = {}
_h3_geometry_lock = threading.Lock()
_records_memory_cache: dict = {}
_records_memory_lock = threading.Lock()
_RECORDS_MEMORY_MAX = 64
_genus_roster_cache: dict = {}
_genus_roster_lock = threading.Lock()


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


def _genus_token(scientific_name: Optional[str]) -> Optional[str]:
    """First genus-like token in a scientific name."""
    if not scientific_name:
        return None
    for raw in scientific_name.replace(",", " ").split():
        token = raw.strip("()[]{}.;:").strip()
        if not token:
            continue
        key = token.lower().rstrip(".")
        if key in _GENUS_QUALIFIERS:
            continue
        if len(token) < 2:
            continue
        if not all(ch.isalpha() or ch == "-" for ch in token):
            continue
        return token
    return None


def _is_species_name(species: str, genus: str) -> bool:
    parts = species.split()
    if len(parts) < 2 or parts[0].lower() != genus.lower():
        return False
    epithet = parts[1].strip("()[]{}.;:").lower().rstrip(".")
    return bool(epithet) and epithet not in _GENUS_QUALIFIERS


def _ilike_prefix(genus: str) -> str:
    escaped = genus.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped + " %"


def _species_name_for_aphiaid(aphiaid: int) -> Optional[str]:
    parquet_paths = _speciesgrids_parquet_paths()
    conn = open_spatial_connection()
    row = conn.execute(
        """
        SELECT species
        FROM read_parquet(?)
        WHERE AphiaID = ?
        LIMIT 1
        """,
        [parquet_paths if len(parquet_paths) > 1 else parquet_paths[0], int(aphiaid)],
    ).fetchone()
    if row is None or not row[0]:
        return None
    return str(row[0])


def _genus_roster(genus: str) -> List[dict]:
    """Distinct species whose name starts with this genus, from speciesgrids."""
    key = genus.lower()
    with _genus_roster_lock:
        cached = _genus_roster_cache.get(key)
    if cached is not None:
        return cached

    parquet_paths = _speciesgrids_parquet_paths()
    conn = open_spatial_connection()
    rows = conn.execute(
        """
        SELECT AphiaID, any_value(species) AS species, sum(records) AS records
        FROM read_parquet(?)
        WHERE species ILIKE ? ESCAPE '!'
        GROUP BY AphiaID
        """,
        [
            parquet_paths if len(parquet_paths) > 1 else parquet_paths[0],
            _ilike_prefix(genus),
        ],
    ).fetchall()

    roster = []
    for aid, species, records in rows:
        if not species or not _is_species_name(str(species), genus):
            continue
        roster.append(
            {
                "aphiaid": int(aid),
                "scientificName": str(species),
                "records": int(records or 0),
            }
        )

    with _genus_roster_lock:
        _genus_roster_cache[key] = roster
    return roster


def _density_summary(
    aphiaids: List[int],
    occurrence_h3: Optional[int],
) -> dict:
    """cell count, density at the point, and density in the nearby ring."""
    if not aphiaids:
        return {}

    if not density_path_ok():
        raise FileNotFoundError(f"Density product not found: {DENSITY_PATH}")

    min_u16 = int(math.ceil(CONGENER_MIN_DENSITY * DENSITY_SCALE))
    conn = open_h3_connection()
    if occurrence_h3 is None:
        rows = conn.execute(
            """
            SELECT AphiaID, count(*) AS n_cells
            FROM read_parquet(?)
            WHERE AphiaID IN (SELECT UNNEST(?::INTEGER[]))
              AND density >= ?
            GROUP BY AphiaID
            """,
            [DENSITY_PATH, aphiaids, min_u16],
        ).fetchall()
        return {
            int(aid): {"n_cells": int(n_cells), "at": 0, "near": 0}
            for aid, n_cells in rows
        }

    disk = [
        h3.str_to_int(cell)
        for cell in h3.grid_disk(h3.int_to_str(int(occurrence_h3)), CONGENER_NEAR_RINGS)
    ]
    rows = conn.execute(
        """
        SELECT
            AphiaID,
            count(*) AS n_cells,
            max(CASE WHEN h3 = ? THEN density ELSE 0 END) AS at_cell,
            max(CASE WHEN list_contains(?::UBIGINT[], h3) THEN density ELSE 0 END) AS near_cell
        FROM read_parquet(?)
        WHERE AphiaID IN (SELECT UNNEST(?::INTEGER[]))
          AND density >= ?
        GROUP BY AphiaID
        """,
        [int(occurrence_h3), disk, DENSITY_PATH, aphiaids, min_u16],
    ).fetchall()
    return {
        int(aid): {"n_cells": int(n_cells), "at": int(at_cell or 0), "near": int(near_cell or 0)}
        for aid, n_cells, at_cell, near_cell in rows
    }


def _choose_congeners(
    species: List[dict],
    has_location: bool,
) -> Tuple[List[dict], str, int]:
    """Pick which congeners to draw. Returns (chosen, selection, nearby_count)."""
    nearby_count = sum(1 for sp in species if sp["near"] > 0)
    if not species:
        return [], "all", 0
    if len(species) <= CONGENER_MAX_SPECIES:
        chosen = list(species)
        selection = "all"
    elif has_location and nearby_count:
        chosen = [sp for sp in species if sp["near"] > 0]
        chosen.sort(key=lambda sp: (sp["at"], sp["near"], sp["records"]), reverse=True)
        chosen = chosen[:CONGENER_MAX_SPECIES]
        selection = "nearby"
    else:
        chosen = sorted(species, key=lambda sp: (sp["records"], sp["n_cells"]), reverse=True)
        chosen = chosen[:CONGENER_MAX_SPECIES]
        selection = "recorded"

    chosen.sort(
        key=lambda sp: (
            sp["at"] <= 0,
            sp["near"] <= 0,
            sp["scientificName"].lower(),
        )
    )
    return chosen, selection, nearby_count


def _assign_congener_colors(species: List[dict]) -> None:
    for index, sp in enumerate(species):
        sp["color"] = CONGENER_COLORS[index % len(CONGENER_COLORS)]


def _vertex_key(point) -> Tuple[float, float]:
    return (round(float(point[0]), 5), round(float(point[1]), 5))


def _split_dateline(points: List[List[float]]) -> List[List[List[float]]]:
    """Break a line so no segment is drawn the long way across ±180°."""
    if len(points) < 2:
        return []
    parts: List[List[List[float]]] = []
    current = [[float(points[0][0]), float(points[0][1])]]
    for raw in points[1:]:
        lon2, lat2 = float(raw[0]), float(raw[1])
        lon1, lat1 = current[-1]
        dlon = lon2 - lon1
        if abs(dlon) <= 180:
            current.append([lon2, lat2])
            continue
        lon2_unwrapped = lon2 - 360.0 if dlon > 180 else lon2 + 360.0
        if abs(lon2_unwrapped - lon1) < 1e-6:
            current.append([lon1, lat2])
            continue
        boundary = 180.0 if lon2_unwrapped > lon1 else -180.0
        span = lon2_unwrapped - lon1
        t = (boundary - lon1) / span
        lat_cross = lat1 + t * (lat2 - lat1)
        current.append([boundary, lat_cross])
        parts.append(current)
        other = -boundary
        current = [[other, lat_cross], [lon2, lat2]]
    if len(current) >= 2:
        parts.append(current)
    return parts


def _chain_lines(segments: List[List[List[float]]]) -> List[List[List[float]]]:
    """Join boundary edges that share a vertex into continuous lines."""
    adjacency: dict = {}
    for segment in segments:
        for start, end in zip(segment, segment[1:]):
            start_key = _vertex_key(start)
            end_key = _vertex_key(end)
            if start_key == end_key:
                continue
            adjacency.setdefault(start_key, []).append(end_key)
            adjacency.setdefault(end_key, []).append(start_key)

    lines = []
    for start in list(adjacency):
        while adjacency.get(start):
            nxt = adjacency[start].pop()
            adjacency[nxt].remove(start)
            path = [start, nxt]
            cursor = nxt
            while adjacency.get(cursor):
                nxt = adjacency[cursor].pop()
                adjacency[nxt].remove(cursor)
                path.append(nxt)
                cursor = nxt
                if cursor == start:
                    break
            lines.append([[lon, lat] for lon, lat in path])
    return lines


def _contour_geometry(h3_ints: List[int]) -> Optional[dict]:
    """Outline of the cells as lines, without antimeridian closure edges."""
    if not h3_ints:
        return None
    cells = [h3.int_to_str(int(cell)) for cell in dict.fromkeys(h3_ints)]
    cell_set = set(cells)
    segments: List[List[List[float]]] = []
    try:
        for cell in cells:
            for edge in h3.origin_to_directed_edges(cell):
                neighbor = h3.get_directed_edge_destination(edge)
                if neighbor in cell_set:
                    continue
                boundary = h3.directed_edge_to_boundary(edge)
                segment = [[lng, lat] for lat, lng in boundary]
                segments.extend(_split_dateline(segment))
    except Exception:
        logger.exception("Failed to build congener contour from %s cells", len(cells))
        return None

    lines = _chain_lines(segments)
    if not lines:
        return None
    if len(lines) == 1:
        return {"type": "LineString", "coordinates": lines[0]}
    return {"type": "MultiLineString", "coordinates": lines}


def _contour_cells(aphiaids: List[int]) -> dict:
    min_u16 = int(math.ceil(CONGENER_MIN_DENSITY * DENSITY_SCALE))
    conn = open_h3_connection()
    rows = conn.execute(
        """
        SELECT AphiaID, h3
        FROM read_parquet(?)
        WHERE AphiaID IN (SELECT UNNEST(?::INTEGER[]))
          AND density >= ?
        """,
        [DENSITY_PATH, aphiaids, min_u16],
    ).fetchall()
    grouped: dict = {}
    for aid, cell in rows:
        grouped.setdefault(int(aid), []).append(int(cell))
    return grouped


def _congener_cache_key(genus: str, aphiaid: int, occurrence_h3: Optional[int]) -> str:
    logical = "|".join(
        [
            "congeners",
            genus.lower(),
            str(int(aphiaid)),
            str(occurrence_h3) if occurrence_h3 is not None else "none",
            f"{CONGENER_MIN_DENSITY:.4f}",
            str(CONGENER_MAX_SPECIES),
            str(CONGENER_NEAR_RINGS),
            "colors3",
            "lines",
        ]
    )
    digest = hashlib.sha1(logical.encode("utf-8")).hexdigest()[:20]
    return f"congener_v{MAP_CACHE_VERSION}_{digest}"


def _empty_congener_payload(genus: Optional[str]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [],
        "genus": genus,
        "matched": 0,
        "nearby": 0,
        "shown": 0,
        "selection": "all",
        "cutoff": CONGENER_MIN_DENSITY,
    }


def get_congener_geojson(
    aphiaid: int,
    scientific_name: Optional[str] = None,
    lon: Optional[float] = None,
    lat: Optional[float] = None,
) -> dict:
    """Contours of congeners found by a prefix search on scientific names."""
    aid = int(aphiaid)
    started = time.perf_counter()
    genus = _genus_token(scientific_name)
    roster = _genus_roster(genus) if genus else []
    others = [sp for sp in roster if sp["aphiaid"] != aid]
    if not others:
        canonical = _species_name_for_aphiaid(aid)
        canonical_genus = _genus_token(canonical)
        if canonical_genus and canonical_genus.lower() != (genus or "").lower():
            genus = canonical_genus
            roster = _genus_roster(genus)
            others = [sp for sp in roster if sp["aphiaid"] != aid]

    if not genus:
        return _empty_congener_payload(None)

    occurrence_h3 = None
    if lon is not None and lat is not None:
        try:
            occurrence_h3 = latlng_to_h3_int(float(lat), float(lon))
        except Exception:
            logger.warning("Invalid congener map coordinates lon=%s lat=%s", lon, lat)

    cache_key = _congener_cache_key(genus, aid, occurrence_h3)
    cached = get_cached_json(cache_key)
    if cached is not None:
        return cached

    if not others:
        payload = _empty_congener_payload(genus)
        store_cached_json(cache_key, payload)
        return payload

    summary = _density_summary([sp["aphiaid"] for sp in others], occurrence_h3)
    candidates = []
    for sp in others:
        stats = summary.get(sp["aphiaid"])
        if not stats or stats["n_cells"] <= 0:
            continue
        candidates.append({**sp, **stats})

    chosen, selection, nearby_count = _choose_congeners(
        candidates,
        has_location=occurrence_h3 is not None,
    )
    _assign_congener_colors(chosen)
    cells_by_id = _contour_cells([sp["aphiaid"] for sp in chosen]) if chosen else {}

    features = []
    for rank, sp in enumerate(chosen):
        geometry = _contour_geometry(cells_by_id.get(sp["aphiaid"], []))
        if geometry is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "aphiaid": sp["aphiaid"],
                    "scientificName": sp["scientificName"],
                    "color": sp["color"],
                    "rank": rank,
                },
            }
        )

    payload = {
        "type": "FeatureCollection",
        "features": features,
        "genus": genus,
        "matched": len(candidates),
        "nearby": nearby_count,
        "shown": len(features),
        "selection": selection if features else "all",
        "cutoff": CONGENER_MIN_DENSITY,
    }
    store_cached_json(cache_key, payload)
    logger.info(
        "Built congener contours for %s AphiaID %s: %s of %s in %.2fs",
        genus,
        aid,
        len(features),
        len(candidates),
        time.perf_counter() - started,
    )
    return payload
