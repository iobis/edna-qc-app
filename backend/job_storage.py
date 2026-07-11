import json
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_JOBS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "jobs",
)
JOBS_DIR = os.environ.get(
    "JOBS_DIR",
    "/data/jobs" if os.path.isdir("/data/jobs") else DEFAULT_JOBS_DIR,
)
JOB_META_TTL_SECONDS = int(os.environ.get("JOB_META_TTL_SECONDS", str(3600)))
JOB_FAILED_TTL_SECONDS = int(os.environ.get("JOB_FAILED_TTL_SECONDS", str(86400)))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def job_dir(job_id: str) -> str:
    return os.path.join(JOBS_DIR, job_id)


def input_dir(job_id: str) -> str:
    return os.path.join(job_dir(job_id), "input")


def save_job_input(job_id: str, files_data: List[Dict[str, Any]]) -> None:
    directory = input_dir(job_id)
    os.makedirs(directory, exist_ok=True)
    manifest = []
    for index, item in enumerate(files_data):
        filename = item["filename"]
        safe_name = f"{index:04d}_{filename.replace('/', '_')}"
        path = os.path.join(directory, safe_name)
        content = item["content"]
        if isinstance(content, str):
            content = content.encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(content)
        manifest.append({"filename": filename, "stored_as": safe_name})
    with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)


def load_job_input(job_id: str) -> List[Dict[str, Any]]:
    directory = input_dir(job_id)
    manifest_path = os.path.join(directory, "manifest.json")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    files_data = []
    for entry in manifest:
        path = os.path.join(directory, entry["stored_as"])
        with open(path, "rb") as handle:
            files_data.append({
                "filename": entry["filename"],
                "content": handle.read(),
            })
    return files_data


def cleanup_job_input(job_id: str) -> None:
    directory = input_dir(job_id)
    if os.path.isdir(directory):
        shutil.rmtree(directory, ignore_errors=True)
        logger.info("Removed input files for job %s", job_id)


def remove_job_dir(job_id: str) -> None:
    directory = job_dir(job_id)
    if os.path.isdir(directory):
        shutil.rmtree(directory, ignore_errors=True)
        logger.info("Removed job directory %s", job_id)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def should_prune_job(status: str, finished_at: Optional[str]) -> bool:
    finished = _parse_iso(finished_at)
    if finished is None:
        return False
    age = (_utcnow() - finished).total_seconds()
    if status == "failed":
        return age > JOB_FAILED_TTL_SECONDS
    if status == "completed":
        return age > JOB_META_TTL_SECONDS
    return False
