import io
import logging
import os
import zipfile
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

ALLOWED_TEXT_EXTENSIONS = {".txt", ".csv", ".tsv"}
ALLOWED_EXTENSIONS = ALLOWED_TEXT_EXTENSIONS | {".zip"}


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


def collect_upload_payload(
    file_items: List[Tuple[str, bytes, Optional[str]]],
    url: Optional[str] = None,
) -> Tuple[List[Dict], List[Dict], List[str]]:
    """
    Build file_infos and files_data from raw uploads.

    file_items: list of (filename, content, content_type)
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
        logger.info("Downloading zip file from URL: %s", url)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        if (
            not url.lower().endswith(".zip")
            and not response.headers.get("content-type", "").startswith("application/zip")
        ):
            raise ValueError("URL must point to a zip file")
        extracted = extract_text_files_from_zip(response.content, url)
        if not extracted:
            raise ValueError("Zip file from URL contains no valid text files (.txt, .csv, .tsv)")
        for extracted_file in extracted:
            file_infos.append({
                "filename": extracted_file["filename"],
                "content_type": "application/octet-stream",
                "size": len(extracted_file["content"]),
                "source_url": url,
            })
            files_data.append(extracted_file)

    return file_infos, files_data, invalid_files
