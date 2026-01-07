from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional, Dict
import os
import logging
import requests
import zipfile
import io
from parsing import process_uploaded_files

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Backend API")

allowed_origins_str = os.environ.get("CORS_ALLOWED_ORIGINS", "")
if allowed_origins_str:
    allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]
else:
    allowed_origins = ["http://localhost", "https://ednaqc.obis.org"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Backend API is running"}


ALLOWED_TEXT_EXTENSIONS = {'.txt', '.csv', '.tsv'}
ALLOWED_EXTENSIONS = ALLOWED_TEXT_EXTENSIONS | {'.zip'}


def extract_text_files_from_zip(zip_content: bytes, source_name: str = "zip file") -> List[Dict]:
    """
    Extract text files from a zip file content.
    
    Args:
        zip_content: Bytes content of the zip file
        source_name: Name of the source (for error messages)
    
    Returns:
        List of dicts with 'filename' and 'content' keys for extracted text files
    
    Raises:
        zipfile.BadZipFile: If the content is not a valid zip file
    """
    extracted_files = []
    
    with zipfile.ZipFile(io.BytesIO(zip_content)) as zip_file:
        for zip_info in zip_file.namelist():
            filename = os.path.basename(zip_info)
            ext = os.path.splitext(filename)[1].lower()
            
            if ext in ALLOWED_TEXT_EXTENSIONS:
                content = zip_file.read(zip_info)
                extracted_files.append({
                    "filename": filename,
                    "content": content
                })
            elif not zip_info.endswith('/'):
                logger.info(f"Skipping non-text file in {source_name}: {zip_info}")
    
    return extracted_files


@app.post("/api/upload")
async def upload_files(
    files: List[UploadFile] = File(default=[]),
    url: Optional[str] = Form(default=None)
):
    """
    Accept multiple uploaded files (txt, csv, tsv, or zip) or a URL to a zip file.
    Zip files are automatically extracted and their text files are processed.
    Looks for occurrence files and parses them.
    """
    file_infos = []
    invalid_files = []
    files_data = []
    
    # Handle file uploads
    if files:
        for file in files:
            filename = file.filename or ""
            ext = os.path.splitext(filename)[1].lower()
            
            if ext not in ALLOWED_EXTENSIONS:
                invalid_files.append(filename)
                continue
            
            content = await file.read()
            
            # If it's a zip file, extract text files from it
            if ext == '.zip':
                try:
                    extracted = extract_text_files_from_zip(content, filename)
                    if not extracted:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Zip file '{filename}' contains no valid text files (.txt, .csv, .tsv)"
                        )
                    for extracted_file in extracted:
                        file_infos.append({
                            "filename": extracted_file["filename"],
                            "content_type": "application/octet-stream",
                            "size": len(extracted_file["content"]),
                            "source_zip": filename
                        })
                        files_data.append(extracted_file)
                except zipfile.BadZipFile:
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{filename}' is not a valid zip file"
                    )
            else:
                # Regular text file
                file_infos.append({
                    "filename": file.filename,
                    "content_type": file.content_type,
                    "size": len(content)
                })
                files_data.append({
                    "filename": file.filename,
                    "content": content
                })
    
    # Handle URL to zip file
    if url:
        try:
            logger.info(f"Downloading zip file from URL: {url}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            if not url.lower().endswith('.zip') and not response.headers.get('content-type', '').startswith('application/zip'):
                raise HTTPException(
                    status_code=400,
                    detail="URL must point to a zip file"
                )
            
            extracted = extract_text_files_from_zip(response.content, url)
            if not extracted:
                raise HTTPException(
                    status_code=400,
                    detail=f"Zip file from URL contains no valid text files (.txt, .csv, .tsv)"
                )
            for extracted_file in extracted:
                file_infos.append({
                    "filename": extracted_file["filename"],
                    "content_type": "application/octet-stream",
                    "size": len(extracted_file["content"]),
                    "source_url": url
                })
                files_data.append(extracted_file)
        except requests.RequestException as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to download file from URL: {str(e)}"
            )
        except zipfile.BadZipFile:
            raise HTTPException(
                status_code=400,
                detail="URL does not point to a valid zip file"
            )
    
    if not files_data:
        raise HTTPException(
            status_code=400,
            detail="No valid files provided. Please upload files or provide a URL to a zip file."
        )
    
    if invalid_files:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file types. Only {', '.join(sorted(ALLOWED_EXTENSIONS))} files are allowed. Invalid files: {', '.join(invalid_files)}"
        )
    
    try:
        processing_result = process_uploaded_files(files_data)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error processing files: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error processing files: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while processing files: {str(e)}"
        )
    
    return {
        "files_received": len(file_infos),
        "files": file_infos,
        "processing": processing_result
    }

