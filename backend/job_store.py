import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from job_storage import JOBS_DIR, remove_job_dir, should_prune_job

logger = logging.getLogger(__name__)

JOBS_DB_PATH = os.environ.get("JOBS_DB_PATH", os.path.join(JOBS_DIR, "jobs.sqlite"))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(JOBS_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(JOBS_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                cache_key TEXT NOT NULL,
                status TEXT NOT NULL,
                file_infos TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_cache_key_status ON jobs(cache_key, status)"
        )
        conn.commit()


@contextmanager
def _transaction():
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_job(
    cache_key: str,
    file_infos: List[Dict[str, Any]],
    job_id: Optional[str] = None,
) -> str:
    if not job_id:
        job_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, cache_key, status, file_infos, created_at)
            VALUES (?, ?, 'queued', ?, ?)
            """,
            (job_id, cache_key, json.dumps(file_infos), _utcnow_iso()),
        )
        conn.commit()
    return job_id


def find_active_job_by_cache_key(cache_key: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE cache_key = ? AND status IN ('queued', 'running')
            ORDER BY created_at
            LIMIT 1
            """,
            (cache_key,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_queue_position(job_id: str) -> Optional[int]:
    job = get_job(job_id)
    if not job or job["status"] != "queued":
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS ahead FROM jobs
            WHERE status = 'queued' AND created_at < ?
            """,
            (job["created_at"],),
        ).fetchone()
    return int(row["ahead"]) if row else None


def claim_next_job() -> Optional[Dict[str, Any]]:
    with _transaction() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'queued'
            ORDER BY created_at
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        started_at = _utcnow_iso()
        updated = conn.execute(
            """
            UPDATE jobs
            SET status = 'running', started_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (started_at, row["id"]),
        )
        if updated.rowcount != 1:
            return None
        job = dict(row)
        job["status"] = "running"
        job["started_at"] = started_at
        return _row_to_dict(job)


def mark_completed(job_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'completed', finished_at = ?, error = NULL
            WHERE id = ?
            """,
            (_utcnow_iso(), job_id),
        )
        conn.commit()


def mark_failed(job_id: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'failed', finished_at = ?, error = ?
            WHERE id = ?
            """,
            (_utcnow_iso(), error, job_id),
        )
        conn.commit()


def delete_job(job_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
    remove_job_dir(job_id)


def prune_finished_jobs() -> int:
    removed = 0
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, status, finished_at FROM jobs
            WHERE status IN ('completed', 'failed') AND finished_at IS NOT NULL
            """
        ).fetchall()
    for row in rows:
        if should_prune_job(row["status"], row["finished_at"]):
            delete_job(row["id"])
            removed += 1
    if removed:
        logger.info("Pruned %s finished jobs", removed)
    return removed


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "cache_key": row["cache_key"],
        "status": row["status"],
        "file_infos": json.loads(row["file_infos"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def job_to_api_response(job: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "job_id": job["id"],
        "status": job["status"],
        "cache_key": job["cache_key"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "error": job["error"],
        "files": job["file_infos"],
        "files_received": len(job["file_infos"]),
    }
    if job["status"] == "queued":
        position = get_queue_position(job["id"])
        response["position"] = position if position is not None else 0
    if result is not None:
        response["result"] = result
        response["processing"] = result.get("processing")
        response["cached"] = result.get("cached", False)
        if result.get("files_received") is not None:
            response["files_received"] = result["files_received"]
        if result.get("files") is not None:
            response["files"] = result["files"]
    return response
