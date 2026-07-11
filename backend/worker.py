import logging
import time

from job_storage import cleanup_job_input, load_job_input, save_job_input
from job_store import claim_next_job, init_db, mark_completed, mark_failed, prune_finished_jobs
from parsing import process_uploaded_files
from result_cache import get_cached_response, prune_expired_cache, store_cached_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = float(__import__("os").environ.get("WORKER_POLL_INTERVAL_SECONDS", "1"))


def process_job(job: dict) -> None:
    job_id = job["id"]
    cache_key = job["cache_key"]
    file_infos = job["file_infos"]

    cached = get_cached_response(cache_key)
    if cached is not None:
        logger.info("Job %s already cached, marking completed", job_id)
        cleanup_job_input(job_id)
        mark_completed(job_id)
        return

    try:
        files_data = load_job_input(job_id)
        processing_result = process_uploaded_files(files_data)
        response = {
            "files_received": len(file_infos),
            "files": file_infos,
            "processing": processing_result,
            "cached": False,
            "cache_key": cache_key,
        }
        store_cached_response(cache_key, response)
        mark_completed(job_id)
        logger.info("Job %s completed successfully", job_id)
    except ValueError as exc:
        mark_failed(job_id, str(exc))
        logger.warning("Job %s failed: %s", job_id, exc)
    except Exception as exc:
        mark_failed(job_id, f"Internal server error while processing files: {exc}")
        logger.error("Job %s failed unexpectedly", job_id, exc_info=True)
    finally:
        cleanup_job_input(job_id)
        prune_expired_cache()
        prune_finished_jobs()


def run_worker() -> None:
    init_db()
    logger.info("Worker started (serial processing, poll interval %.1fs)", POLL_INTERVAL_SECONDS)

    while True:
        job = claim_next_job()
        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            prune_finished_jobs()
            continue
        logger.info("Processing job %s", job["id"])
        process_job(job)


if __name__ == "__main__":
    run_worker()
