"""Speciesgrids density + thermal suitability products (H3 resolution 3)."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import logging
import os
import threading

import duckdb
import h3
import numpy as np
import pyarrow.parquet as pq
from scipy.stats import gaussian_kde

logger = logging.getLogger(__name__)

H3_RESOLUTION = 3
DENSITY_SCALE = 65535

_DEFAULT_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)


def _default_path(*parts: str) -> str:
    container = os.path.join("/data", *parts)
    if os.path.exists(container):
        return container
    return os.path.join(_DEFAULT_DATA, *parts)


DENSITY_PATH = os.environ.get(
    "DENSITY_PATH",
    _default_path("density", "data.parquet"),
)
THERMAL_THETAO_PATH = os.environ.get(
    "THERMAL_THETAO_PATH",
    _default_path("thermal", "thetao.parquet"),
)
THERMAL_PROFILES_PATH = os.environ.get(
    "THERMAL_PROFILES_PATH",
    _default_path("thermal", "profiles.parquet"),
)

# Backwards-compatible aliases used by older imports / compose.
SPEEDY_RESOLUTION = H3_RESOLUTION
SPEEDY_DATA_DIR = os.environ.get("SPEEDY_DATA_DIR", os.path.dirname(DENSITY_PATH))

_thetao_lock = threading.Lock()
_thetao_state: Optional[dict] = None
_profile_lock = threading.Lock()
_profile_cache: Dict[int, Optional[dict]] = {}
_suitability_lock = threading.Lock()
_suitability_cache: Dict[int, List[Tuple[int, float]]] = {}
_duckdb_init_lock = threading.Lock()
_thread_local = threading.local()


def decode_density(density_u16) -> float:
    return float(density_u16) / DENSITY_SCALE


def h3_to_str(h3_index) -> str:
    return h3.int_to_str(int(h3_index))


def density_path_ok() -> bool:
    return os.path.isfile(DENSITY_PATH)


def thermal_paths_ok() -> bool:
    return os.path.isfile(THERMAL_THETAO_PATH) and os.path.isfile(THERMAL_PROFILES_PATH)


def _load_thetao() -> dict:
    global _thetao_state
    with _thetao_lock:
        if _thetao_state is not None:
            return _thetao_state
        if not os.path.isfile(THERMAL_THETAO_PATH):
            raise FileNotFoundError(f"Thermal thetao map not found: {THERMAL_THETAO_PATH}")
        table = pq.read_table(THERMAL_THETAO_PATH, columns=["h3", "thetao"])
        h3_ids = np.asarray(table.column("h3").to_pylist(), dtype=object)
        h3_ints = np.array([int(x) for x in h3_ids], dtype=np.uint64)
        thetao = np.asarray(table.column("thetao").to_numpy(zero_copy_only=False), dtype=np.float64)
        by_h3 = {int(cell): float(val) for cell, val in zip(h3_ints, thetao)}
        _thetao_state = {"h3": h3_ints, "thetao": thetao, "by_h3": by_h3}
        logger.info(
            "Loaded thermal thetao map: %s cells from %s",
            len(h3_ints),
            THERMAL_THETAO_PATH,
        )
        return _thetao_state


def get_profile(aphiaid: int) -> Optional[dict]:
    aid = int(aphiaid)
    with _profile_lock:
        if aid in _profile_cache:
            return _profile_cache[aid]

    if not os.path.isfile(THERMAL_PROFILES_PATH):
        raise FileNotFoundError(f"Thermal profiles not found: {THERMAL_PROFILES_PATH}")

    table = pq.read_table(
        THERMAL_PROFILES_PATH,
        filters=[("AphiaID", "=", aid)],
        columns=["AphiaID", "bandwidth", "temps", "norm_max"],
    )
    if table.num_rows == 0:
        profile = None
    else:
        row = table.slice(0, 1).to_pydict()
        temps = np.asarray(row["temps"][0], dtype=np.float64)
        profile = {
            "temps": temps,
            "bandwidth": float(row["bandwidth"][0]),
            "norm_max": float(row["norm_max"][0]),
        }

    with _profile_lock:
        _profile_cache[aid] = profile
    return profile


def suitability_at_cell(aphiaid: int, h3_cell: int, kde_cache: Optional[dict] = None) -> Optional[float]:
    profile = get_profile(aphiaid)
    if profile is None:
        return None
    thetao_val = _load_thetao()["by_h3"].get(int(h3_cell))
    if thetao_val is None or not np.isfinite(thetao_val):
        return None
    norm_max = profile["norm_max"]
    if not norm_max or not np.isfinite(norm_max):
        return None

    cache = kde_cache if kde_cache is not None else {}
    aid = int(aphiaid)
    if aid not in cache:
        cache[aid] = gaussian_kde(profile["temps"], bw_method=profile["bandwidth"])
    value = float(cache[aid].evaluate([thetao_val])[0] / norm_max)
    return value


def suitability_rows_for_aphiaid(aphiaid: int) -> List[Tuple[int, float]]:
    """Return (h3_uint64, suitability) for all finite SST cells."""
    aid = int(aphiaid)
    with _suitability_lock:
        cached = _suitability_cache.get(aid)
    if cached is not None:
        return cached

    profile = get_profile(aid)
    if profile is None:
        raise FileNotFoundError(f"No thermal profile for AphiaID {aphiaid}")

    thetao = _load_thetao()
    temps = profile["temps"]
    bandwidth = profile["bandwidth"]
    norm_max = profile["norm_max"]
    if not norm_max or not np.isfinite(norm_max):
        raise ValueError(f"Invalid norm_max for AphiaID {aphiaid}")

    kde = gaussian_kde(temps, bw_method=bandwidth)
    sst = thetao["thetao"]
    finite = np.isfinite(sst)
    suit = np.full(len(sst), np.nan, dtype=np.float64)
    if finite.any():
        suit[finite] = kde.evaluate(sst[finite]) / norm_max

    rows: List[Tuple[int, float]] = []
    for cell, value in zip(thetao["h3"], suit):
        if np.isfinite(value):
            rows.append((int(cell), float(value)))

    with _suitability_lock:
        _suitability_cache[aid] = rows
    return rows


def open_h3_connection() -> duckdb.DuckDBPyConnection:
    """Return a thread-local DuckDB connection with the h3 extension loaded."""
    conn = getattr(_thread_local, "h3_conn", None)
    if conn is not None:
        return conn
    with _duckdb_init_lock:
        conn = getattr(_thread_local, "h3_conn", None)
        if conn is not None:
            return conn
        conn = duckdb.connect(database=":memory:")
        conn.execute("INSTALL h3 FROM community;")
        conn.execute("LOAD h3;")
        _thread_local.h3_conn = conn
        logger.info("Opened thread-local DuckDB h3 connection")
        return conn


def open_spatial_connection() -> duckdb.DuckDBPyConnection:
    """Return a thread-local DuckDB connection with the spatial extension loaded."""
    conn = getattr(_thread_local, "spatial_conn", None)
    if conn is not None:
        return conn
    with _duckdb_init_lock:
        conn = getattr(_thread_local, "spatial_conn", None)
        if conn is not None:
            return conn
        conn = duckdb.connect(database=":memory:")
        conn.execute("INSTALL spatial;")
        conn.execute("LOAD spatial;")
        _thread_local.spatial_conn = conn
        logger.info("Opened thread-local DuckDB spatial connection")
        return conn


def latlng_to_h3(conn: duckdb.DuckDBPyConnection, lat: float, lon: float) -> int:
    return int(
        conn.execute(
            "SELECT h3_latlng_to_cell(?, ?, ?)",
            [float(lat), float(lon), H3_RESOLUTION],
        ).fetchone()[0]
    )


def density_at_cell(
    conn: duckdb.DuckDBPyConnection,
    aphiaid: int,
    h3_cell: int,
) -> Optional[float]:
    values = densities_for_lookups(conn, [(int(aphiaid), int(h3_cell))])
    return values.get((int(aphiaid), int(h3_cell)))


def densities_for_lookups(
    conn: duckdb.DuckDBPyConnection,
    lookups: List[Tuple[int, int]],
) -> Dict[Tuple[int, int], float]:
    """Batch-fetch densities for (AphiaID, h3) pairs in one parquet scan.

    Cells omitted from the density product (values below the store cutoff) are
    returned as ``0.0`` when the AphiaID has any density rows at all. AphiaIDs
    absent from the product are left out of the result (caller treats as no data).
    """
    if not lookups:
        return {}
    if not density_path_ok():
        return {}

    unique = sorted({(int(aphiaid), int(h3_cell)) for aphiaid, h3_cell in lookups})
    conn.execute("CREATE OR REPLACE TEMP TABLE _density_lookups(AphiaID INTEGER, h3 UBIGINT)")
    conn.executemany("INSERT INTO _density_lookups VALUES (?, ?)", unique)
    try:
        rows = conn.execute(
            """
            SELECT l.AphiaID, l.h3, d.density
            FROM _density_lookups l
            LEFT JOIN read_parquet(?) d
              ON d.AphiaID = l.AphiaID AND d.h3 = l.h3
            """,
            [DENSITY_PATH],
        ).fetchall()

        out: Dict[Tuple[int, int], float] = {}
        missing_aphiaids = set()
        for aphiaid, h3_cell, density_u16 in rows:
            key = (int(aphiaid), int(h3_cell))
            if density_u16 is None:
                missing_aphiaids.add(int(aphiaid))
                continue
            out[key] = decode_density(density_u16)

        if missing_aphiaids:
            present_rows = conn.execute(
                """
                SELECT DISTINCT AphiaID
                FROM read_parquet(?)
                WHERE AphiaID IN (SELECT UNNEST(?::INTEGER[]))
                """,
                [DENSITY_PATH, sorted(missing_aphiaids)],
            ).fetchall()
            present = {int(row[0]) for row in present_rows}
            for aphiaid, h3_cell, density_u16 in rows:
                if density_u16 is not None:
                    continue
                key = (int(aphiaid), int(h3_cell))
                if int(aphiaid) in present:
                    out[key] = 0.0
    finally:
        conn.execute("DROP TABLE IF EXISTS _density_lookups")

    return out


def preload_profiles(aphiaids: List[int]) -> None:
    """Warm the profile cache for many AphiaIDs in one parquet read."""
    aids = sorted({int(a) for a in aphiaids if a is not None})
    if not aids:
        return
    if not os.path.isfile(THERMAL_PROFILES_PATH):
        raise FileNotFoundError(f"Thermal profiles not found: {THERMAL_PROFILES_PATH}")

    missing = [aid for aid in aids if aid not in _profile_cache]
    if not missing:
        return

    table = pq.read_table(
        THERMAL_PROFILES_PATH,
        filters=[("AphiaID", "in", missing)],
        columns=["AphiaID", "bandwidth", "temps", "norm_max"],
    )
    found = set()
    if table.num_rows:
        data = table.to_pydict()
        with _profile_lock:
            for i, aid in enumerate(data["AphiaID"]):
                aid_i = int(aid)
                found.add(aid_i)
                _profile_cache[aid_i] = {
                    "temps": np.asarray(data["temps"][i], dtype=np.float64),
                    "bandwidth": float(data["bandwidth"][i]),
                    "norm_max": float(data["norm_max"][i]),
                }

    with _profile_lock:
        for aid in missing:
            if aid not in found:
                _profile_cache[aid] = None


def latlng_to_h3_int(lat: float, lon: float) -> int:
    """H3 cell as uint64 without DuckDB."""
    return h3.str_to_int(h3.latlng_to_cell(float(lat), float(lon), H3_RESOLUTION))


def density_rows_for_aphiaid(
    conn: duckdb.DuckDBPyConnection,
    aphiaid: int,
    min_density: float,
    occurrence_h3: Optional[int] = None,
) -> List[Tuple[int, float]]:
    if not density_path_ok():
        raise FileNotFoundError(f"Density product not found: {DENSITY_PATH}")

    min_u16 = int(np.ceil(float(min_density) * DENSITY_SCALE))
    if occurrence_h3 is not None:
        rows = conn.execute(
            """
            SELECT h3, density
            FROM read_parquet(?)
            WHERE AphiaID = ?
              AND (density >= ? OR h3 = ?)
            """,
            [DENSITY_PATH, int(aphiaid), min_u16, int(occurrence_h3)],
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT h3, density
            FROM read_parquet(?)
            WHERE AphiaID = ?
              AND density >= ?
            """,
            [DENSITY_PATH, int(aphiaid), min_u16],
        ).fetchall()

    return [(int(h3_cell), decode_density(density_u16)) for h3_cell, density_u16 in rows]


def has_density_for_aphiaid(conn: duckdb.DuckDBPyConnection, aphiaid: int) -> bool:
    if not density_path_ok():
        return False
    row = conn.execute(
        "SELECT 1 FROM read_parquet(?) WHERE AphiaID = ? LIMIT 1",
        [DENSITY_PATH, int(aphiaid)],
    ).fetchone()
    return row is not None
