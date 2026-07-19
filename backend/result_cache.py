import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CACHE_VERSION = os.environ.get("RESULT_CACHE_VERSION", "3")
DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "cache",
)
CACHE_DIR = os.environ.get(
    "RESULT_CACHE_DIR",
    "/data/cache" if os.path.isdir("/data/cache") else DEFAULT_CACHE_DIR,
)
CACHE_TTL_SECONDS = int(os.environ.get("RESULT_CACHE_TTL_SECONDS", str(24 * 3600)))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cache_path(cache_key: str) -> str:
    return os.path.join(CACHE_DIR, f"{cache_key}.json")


def compute_submission_hash(files_data: List[Dict[str, Any]]) -> str:
    """Hash filenames and contents to identify an identical submission."""
    hasher = hashlib.sha256()
    hasher.update(CACHE_VERSION.encode("utf-8"))
    hasher.update(b"\0")

    for item in sorted(files_data, key=lambda entry: entry["filename"]):
        hasher.update(item["filename"].encode("utf-8"))
        hasher.update(b"\0")
        content = item["content"]
        if isinstance(content, str):
            content = content.encode("utf-8")
        hasher.update(hashlib.sha256(content).digest())
        hasher.update(b"\0")

    return hasher.hexdigest()


def _is_expired(created_at: datetime) -> bool:
    if CACHE_TTL_SECONDS <= 0:
        return False
    age = (_utcnow() - created_at).total_seconds()
    return age > CACHE_TTL_SECONDS


def _parse_created_at(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def get_cached_response(cache_key: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(cache_key)
    if not os.path.isfile(path):
        return None

    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read cache entry %s: %s", cache_key, exc)
        return None

    created_at = _parse_created_at(payload.get("created_at", ""))
    if created_at is None or _is_expired(created_at):
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    response = payload.get("response")
    if not isinstance(response, dict):
        return None

    response = dict(response)
    response["cached"] = True
    response["cache_key"] = cache_key
    response["cached_at"] = payload["created_at"]
    return response


def _ensure_cache_dir() -> bool:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except OSError as exc:
        logger.warning("Cannot create result cache dir %s: %s", CACHE_DIR, exc)
        return False
    if not os.path.isdir(CACHE_DIR):
        logger.warning("Result cache path is not a directory: %s", CACHE_DIR)
        return False
    return True


def store_cached_response(cache_key: str, response: Dict[str, Any]) -> None:
    if CACHE_TTL_SECONDS <= 0:
        return

    if not _ensure_cache_dir():
        return

    path = _cache_path(cache_key)
    tmp_path = f"{path}.tmp"

    payload = {
        "created_at": _utcnow().isoformat(),
        "cache_key": cache_key,
        "cache_version": CACHE_VERSION,
        "response": response,
    }

    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp_path, path)
        logger.info("Stored processing result in cache: %s", cache_key)
    except OSError as exc:
        logger.warning("Failed to write cache entry %s: %s", cache_key, exc)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def prune_expired_cache() -> int:
    if not os.path.isdir(CACHE_DIR):
        return 0

    removed = 0
    for filename in os.listdir(CACHE_DIR):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(CACHE_DIR, filename)
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            created_at = _parse_created_at(payload.get("created_at", ""))
            if created_at is None or _is_expired(created_at):
                os.remove(path)
                removed += 1
        except (OSError, json.JSONDecodeError):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass

    if removed:
        logger.info("Pruned %s expired cache entries", removed)
    return removed
