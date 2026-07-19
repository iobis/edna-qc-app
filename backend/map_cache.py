"""Disk + memory cache for density/suitability map GeoJSON."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MAP_CACHE_VERSION = os.environ.get("MAP_CACHE_VERSION", "1")
_DEFAULT_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "cache",
)
CACHE_DIR = os.environ.get(
    "RESULT_CACHE_DIR",
    "/data/cache" if os.path.isdir("/data/cache") else _DEFAULT_CACHE,
)
MAP_CACHE_DIR = os.path.join(CACHE_DIR, "maps")

_memory: Dict[str, dict] = {}
_memory_lock = threading.Lock()
_MAX_MEMORY_ENTRIES = 64


def _ensure_dir() -> bool:
    try:
        os.makedirs(MAP_CACHE_DIR, exist_ok=True)
    except OSError as exc:
        logger.warning("Cannot create map cache dir %s: %s", MAP_CACHE_DIR, exc)
        return False
    if not os.path.isdir(MAP_CACHE_DIR):
        logger.warning("Map cache path is not a directory: %s", MAP_CACHE_DIR)
        return False
    return True


def _cache_key(kind: str, aphiaid: int, threshold: float) -> str:
    # threshold quantized to avoid float noise in filenames
    thr = f"{float(threshold):.4f}"
    return f"{kind}_v{MAP_CACHE_VERSION}_{int(aphiaid)}_{thr}"


def _disk_path(key: str) -> str:
    return os.path.join(MAP_CACHE_DIR, f"{key}.json")


def get_cached_map(kind: str, aphiaid: int, threshold: float) -> Optional[dict]:
    key = _cache_key(kind, aphiaid, threshold)
    with _memory_lock:
        hit = _memory.get(key)
        if hit is not None:
            return hit

    path = _disk_path(key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read map cache %s: %s", path, exc)
        return None

    with _memory_lock:
        _remember(key, data)
    return data


def store_cached_map(kind: str, aphiaid: int, threshold: float, payload: dict) -> None:
    key = _cache_key(kind, aphiaid, threshold)
    with _memory_lock:
        _remember(key, payload)

    if not _ensure_dir():
        return

    path = _disk_path(key)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Failed to write map cache %s: %s", path, exc)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _remember(key: str, payload: dict) -> None:
    _memory[key] = payload
    while len(_memory) > _MAX_MEMORY_ENTRIES:
        # drop oldest insertion (CPython 3.7+ dict order)
        oldest = next(iter(_memory))
        if oldest == key:
            break
        del _memory[oldest]


def apply_occurrence_flag(payload: dict, occurrence_h3: Optional[str]) -> dict:
    """Return a shallow-copied FeatureCollection with is_occurrence flags set."""
    features = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        props["is_occurrence"] = bool(
            occurrence_h3 and props.get("h3") == occurrence_h3
        )
        features.append(
            {
                "type": feature.get("type", "Feature"),
                "geometry": feature.get("geometry"),
                "properties": props,
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
    }
