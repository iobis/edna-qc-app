import io
import logging
import os
import time
import zipfile
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.exceptions import ChunkedEncodingError, ConnectionError, Timeout

StatusCallback = Callable[[str], None]

logger = logging.getLogger(__name__)

ALLOWED_TEXT_EXTENSIONS = {".txt", ".csv", ".tsv"}
ALLOWED_EXCEL_EXTENSIONS = {".xlsx"}
ALLOWED_EXTENSIONS = ALLOWED_TEXT_EXTENSIONS | ALLOWED_EXCEL_EXTENSIONS | {".zip"}

# Connect timeout, then per-read idle timeout. Slow IPT mirrors can take many minutes.
URL_DOWNLOAD_TIMEOUT = (30, 600)
URL_DOWNLOAD_CHUNK_SIZE = 1024 * 256
URL_DOWNLOAD_RETRIES = 3
URL_DOWNLOAD_RETRY_BACKOFF_SECONDS = 5


def extract_text_files_from_zip(zip_content: bytes, source_name: str = "zip file") -> List[Dict]:
    extracted_files = []
    with zipfile.ZipFile(io.BytesIO(zip_content)) as zip_file:
        for zip_info in zip_file.namelist():
            filename = os.path.basename(zip_info)
            ext = os.path.splitext(filename)[1].lower()
            if ext in ALLOWED_TEXT_EXTENSIONS:
                content = zip_file.read(zip_info)
                extracted_files.append({"filename": filename, "content": content})
            elif not zip_info.endswith("/"):
                logger.info("Skipping non-text file in %s: %s", source_name, zip_info)
    return extracted_files


def pending_url_file_info(url: str) -> Dict:
    path_name = os.path.basename(urlparse(url).path) or "remote.zip"
    if not path_name.lower().endswith(".zip"):
        path_name = f"{path_name}.zip" if path_name != "remote.zip" else path_name
    return {
        "filename": path_name,
        "content_type": "application/zip",
        "size": None,
        "source_url": url,
        "pending_download": True,
    }


def _stream_zip_once(url: str) -> bytes:
    with requests.get(url, stream=True, timeout=URL_DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if (
            not url.lower().endswith(".zip")
            and not content_type.startswith("application/zip")
            and "zip" not in content_type.lower()
            and "octet-stream" not in content_type.lower()
        ):
            # IPT archive endpoints often omit a zip content-type; allow if body is a zip.
            logger.info("URL content-type is %r; will validate zip magic after download", content_type)

        expected = response.headers.get("Content-Length")
        buffer = io.BytesIO()
        for chunk in response.iter_content(chunk_size=URL_DOWNLOAD_CHUNK_SIZE):
            if chunk:
                buffer.write(chunk)
        content = buffer.getvalue()

    if expected is not None:
        try:
            expected_size = int(expected)
        except ValueError:
            expected_size = None
        if expected_size is not None and len(content) != expected_size:
            raise ConnectionError(
                f"Incomplete download: got {len(content)} of {expected_size} bytes"
            )
    return content


def download_zip_from_url(
    url: str,
    on_status: Optional[StatusCallback] = None,
) -> bytes:
    """Download a remote zip with retries for flaky/slow mirrors."""
    def report(message: str) -> None:
        logger.info("%s", message)
        if on_status:
            on_status(message)

    report(f"Downloading from remote server (attempt 1/{URL_DOWNLOAD_RETRIES})…")
    last_error: Optional[Exception] = None
    for attempt in range(1, URL_DOWNLOAD_RETRIES + 1):
        try:
            if attempt > 1:
                report(f"Downloading from remote server (attempt {attempt}/{URL_DOWNLOAD_RETRIES})…")
            content = _stream_zip_once(url)
            if len(content) < 4 or content[:2] != b"PK":
                raise ValueError("URL must point to a zip file")
            report(f"Downloaded {len(content):,} bytes")
            return content
        except ValueError:
            raise
        except (ChunkedEncodingError, ConnectionError, Timeout, requests.RequestException) as exc:
            last_error = exc
            if attempt >= URL_DOWNLOAD_RETRIES:
                break
            sleep_for = URL_DOWNLOAD_RETRY_BACKOFF_SECONDS * attempt
            report(
                f"Connection failed (attempt {attempt}/{URL_DOWNLOAD_RETRIES}): {exc}. "
                f"Retrying in {sleep_for}s…"
            )
            logger.warning(
                "Download attempt %s/%s failed (%s); retrying in %ss",
                attempt,
                URL_DOWNLOAD_RETRIES,
                exc,
                sleep_for,
            )
            time.sleep(sleep_for)

    raise requests.RequestException(f"Failed after {URL_DOWNLOAD_RETRIES} attempts: {last_error}")


def extract_files_from_url(
    url: str,
    on_status: Optional[StatusCallback] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Download a remote zip and return (file_infos, files_data)."""
    content = download_zip_from_url(url, on_status=on_status)
    if on_status:
        on_status("Extracting files from zip…")
    extracted = extract_text_files_from_zip(content, url)
    if not extracted:
        raise ValueError("Zip file from URL contains no valid text files (.txt, .csv, .tsv)")

    file_infos: List[Dict] = []
    files_data: List[Dict] = []
    for extracted_file in extracted:
        file_infos.append({
            "filename": extracted_file["filename"],
            "content_type": "application/octet-stream",
            "size": len(extracted_file["content"]),
            "source_url": url,
        })
        files_data.append(extracted_file)
    return file_infos, files_data


def collect_upload_payload(
    file_items: List[Tuple[str, bytes, Optional[str]]],
    url: Optional[str] = None,
) -> Tuple[List[Dict], List[Dict], List[str]]:
    """
    Build file_infos and files_data from raw uploads.

    file_items: list of (filename, content, content_type)

    If url is provided it is recorded as a pending download; the actual download
    happens in the worker so slow remotes do not block the HTTP request.
    """
    file_infos: List[Dict] = []
    files_data: List[Dict] = []
    invalid_files: List[str] = []

    for filename, content, content_type in file_items:
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            invalid_files.append(filename)
            continue

        if ext == ".zip":
            extracted = extract_text_files_from_zip(content, filename)
            if not extracted:
                raise ValueError(
                    f"Zip file '{filename}' contains no valid text files (.txt, .csv, .tsv)"
                )
            for extracted_file in extracted:
                file_infos.append({
                    "filename": extracted_file["filename"],
                    "content_type": "application/octet-stream",
                    "size": len(extracted_file["content"]),
                    "source_zip": filename,
                })
                files_data.append(extracted_file)
        else:
            file_infos.append({
                "filename": filename,
                "content_type": content_type or "application/octet-stream",
                "size": len(content),
            })
            files_data.append({"filename": filename, "content": content})

    if url:
        file_infos.append(pending_url_file_info(url))

    return file_infos, files_data, invalid_files
