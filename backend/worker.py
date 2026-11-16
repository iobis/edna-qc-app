import logging
import time
import zipfile

import requests

from job_storage import cleanup_job_input, load_job_input, load_job_source_url
from job_store import (
    claim_next_job,
    init_db,
    mark_completed,
    mark_failed,
    prune_finished_jobs,
    update_job_file_infos,
    update_job_message,
)
from parsing import process_uploaded_files
from result_cache import get_cached_response, prune_expired_cache, store_cached_response
from upload_processing import extract_files_from_url, extract_files_from_zip_bytes
from wilderlab import find_wilderlab_xlsx_files, process_wilderlab_files

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = float(__import__("os").environ.get("WORKER_POLL_INTERVAL_SECONDS", "1"))


def process_job(job: dict) -> None:
    job_id = job["id"]
    cache_key = job["cache_key"]
    file_infos = list(job["file_infos"] or [])

    cached = get_cached_response(cache_key)
    if cached is not None:
        logger.info("Job %s already cached, marking completed", job_id)
        cleanup_job_input(job_id)
        mark_completed(job_id)
        return

    def set_message(message: str) -> None:
        update_job_message(job_id, message)

    try:
        set_message("Starting analysis…")
        files_data = load_job_input(job_id)
        source_url = load_job_source_url(job_id)

        # Expand uploaded zip archives in the worker (not during POST /api/jobs).
        expanded_files: list = []
        expanded_infos: list = [
            info for info in file_infos if not info.get("pending_extract")
        ]
        for item in files_data:
            filename = item.get("filename", "")
            if filename.lower().endswith(".zip"):
                zip_infos, zip_files = extract_files_from_zip_bytes(
                    item["content"],
                    filename,
                    on_status=set_message,
                )
                expanded_files.extend(zip_files)
                expanded_infos.extend(zip_infos)
            else:
                expanded_files.append(item)
        files_data = expanded_files
        if any(info.get("pending_extract") for info in file_infos):
            file_infos = expanded_infos
            update_job_file_infos(job_id, file_infos)

        if source_url:
            logger.info("Job %s downloading source URL: %s", job_id, source_url)
            url_file_infos, url_files_data = extract_files_from_url(
                source_url,
                on_status=set_message,
            )
            files_data.extend(url_files_data)
            file_infos = [
                info for info in file_infos if not info.get("pending_download")
            ] + url_file_infos
            update_job_file_infos(job_id, file_infos)

        if not files_data:
            raise ValueError("No valid files available for processing")

        wilderlab_files = find_wilderlab_xlsx_files(files_data)
        if wilderlab_files:
            processing_result = process_wilderlab_files(
                files_data, on_status=set_message
            )
        else:
            xlsx_files = [
                f["filename"]
                for f in files_data
                if f.get("filename", "").lower().endswith(".xlsx")
            ]
            if xlsx_files:
                raise ValueError(
                    "Unsupported Excel file(s). Only Wilderlab sample-batch XLSX "
                    f"exports are supported. Rejected: {', '.join(xlsx_files)}"
                )
            processing_result = process_uploaded_files(
                files_data, on_status=set_message
            )

        if not processing_result.get("occurrence_file_found") and not processing_result.get(
            "wilderlab_file_found"
        ):
            raise ValueError(
                "No occurrence table found after preparing inputs. "
                "Expected occurrence*.txt/csv/tsv (or a Wilderlab XLSX)."
            )
        if not processing_result.get("analyzed_occurrences"):
            raise ValueError(
                "No species occurrences could be analyzed from the submitted files."
            )

        set_message("Saving results…")
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
    except zipfile.BadZipFile as exc:
        mark_failed(job_id, "Invalid zip file from URL")
        logger.warning("Job %s failed: invalid zip from URL: %s", job_id, exc)
    except requests.RequestException as exc:
        mark_failed(job_id, f"Failed to download file from URL: {exc}")
        logger.warning("Job %s failed downloading URL: %s", job_id, exc)
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
