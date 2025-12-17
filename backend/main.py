from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import os
import logging
from parsing import process_uploaded_files

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Backend API is running"}


ALLOWED_EXTENSIONS = {'.txt', '.csv', '.tsv'}


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    """
    Accept multiple uploaded files (txt, csv, tsv only) and process them.
    Looks for occurrence files and parses them.
    """
    file_infos = []
    invalid_files = []
    files_data = []
    
    for file in files:
        filename = file.filename or ""
        ext = os.path.splitext(filename)[1].lower()
        
        if ext not in ALLOWED_EXTENSIONS:
            invalid_files.append(filename)
            continue
        
        content = await file.read()
        file_infos.append({
            "filename": file.filename,
            "content_type": file.content_type,
            "size": len(content)
        })
        files_data.append({
            "filename": file.filename,
            "content": content
        })
    
    if invalid_files:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file types. Only {', '.join(ALLOWED_EXTENSIONS)} files are allowed. Invalid files: {', '.join(invalid_files)}"
        )
    
    processing_result = process_uploaded_files(files_data)
    
    return {
        "files_received": len(file_infos),
        "files": file_infos,
        "processing": processing_result
    }

