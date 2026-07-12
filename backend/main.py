from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import logging
import zipfile

import requests

from density_map import get_density_geojson, get_speciesgrids_records_geojson
from job_storage import save_job_input
from job_store import (
    create_job,
    find_active_job_by_cache_key,
    get_job,
    init_db,
    job_to_api_response,
    prune_finished_jobs,
)
from result_cache import (
    compute_submission_hash,
    get_cached_response,
    prune_expired_cache,
)
from upload_processing import ALLOWED_EXTENSIONS, collect_upload_payload
from xai import ask_observation_likelihood

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Backend API")

allowed_origins_str = __import__("os").environ.get("CORS_ALLOWED_ORIGINS", "")
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


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def read_root():
    return {"message": "Backend API is running"}


@app.get("/api/density-map/{aphiaid}/records")
def density_map_records(aphiaid: int):
    try:
        return get_speciesgrids_records_geojson(aphiaid)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to load speciesgrids records for {aphiaid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load speciesgrids records: {e}")


@app.get("/api/density-map/{aphiaid}")
def density_map(
    aphiaid: int,
    lon: Optional[float] = Query(default=None),
    lat: Optional[float] = Query(default=None),
):
    try:
        return get_density_geojson(aphiaid, lon=lon, lat=lat)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to load density map for {aphiaid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load density map: {e}")


class SpeciesLikelihoodRequest(BaseModel):
    scientific_name: Optional[str] = None
    aphiaid: Optional[int] = None
    lat: float
    lon: float


@app.post("/api/species-likelihood")
def species_likelihood(body: SpeciesLikelihoodRequest):
    try:
        answer = ask_observation_likelihood(
            scientific_name=body.scientific_name,
            lat=body.lat,
            lon=body.lon,
            aphiaid=body.aphiaid,
        )
        return {"answer": answer}
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except requests.HTTPError as e:
        logger.error("xAI API error: %s", e, exc_info=True)
        detail = "xAI API request failed"
        if e.response is not None:
            try:
                detail = e.response.json().get("error", detail)
            except ValueError:
                detail = e.response.text or detail
        raise HTTPException(status_code=502, detail=detail) from e
    except requests.RequestException as e:
        logger.error("xAI request failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"xAI request failed: {e}") from e
    except Exception as e:
        logger.error("Species likelihood failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Species likelihood failed: {e}") from e


async def _read_submission(
    files: List[UploadFile],
    url: Optional[str],
):
    file_items = []
    for file in files:
        filename = file.filename or ""
        content = await file.read()
        file_items.append((filename, content, file.content_type))

    try:
        file_infos, files_data, invalid_files = collect_upload_payload(file_items, url=url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Failed to download file from URL: {exc}") from exc

    if not files_data:
        raise HTTPException(
            status_code=400,
            detail="No valid files provided. Please upload files or provide a URL to a zip file.",
        )

    if invalid_files:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid file types. Only {', '.join(sorted(ALLOWED_EXTENSIONS))} files are allowed. "
                f"Invalid files: {', '.join(invalid_files)}"
            ),
        )

    return file_infos, files_data


@app.post("/api/jobs")
async def create_analysis_job(
    files: List[UploadFile] = File(default=[]),
    url: Optional[str] = Form(default=None),
):
    file_infos, files_data = await _read_submission(files, url)

    cache_key = compute_submission_hash(files_data)
    cached_response = get_cached_response(cache_key)
    if cached_response is not None:
        logger.info("Serving cached processing result for submission %s", cache_key)
        return job_to_api_response(
            {
                "id": None,
                "cache_key": cache_key,
                "status": "completed",
                "file_infos": file_infos,
                "error": None,
                "created_at": cached_response.get("cached_at"),
                "started_at": cached_response.get("cached_at"),
                "finished_at": cached_response.get("cached_at"),
            },
            result=cached_response,
        )

    existing = find_active_job_by_cache_key(cache_key)
    if existing is not None:
        logger.info("Returning existing active job %s for cache key %s", existing["id"], cache_key)
        return JSONResponse(
            status_code=202,
            content=job_to_api_response(existing),
        )

    job_id = create_job(cache_key, file_infos)
    save_job_input(job_id, files_data)
    job = get_job(job_id)
    prune_expired_cache()
    prune_finished_jobs()

    return JSONResponse(
        status_code=202,
        content=job_to_api_response(job),
    )


@app.get("/api/jobs/{job_id}")
def get_analysis_job(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result = None
    if job["status"] == "completed":
        result = get_cached_response(job["cache_key"])
        if result is None:
            raise HTTPException(
                status_code=500,
                detail="Job completed but result is no longer available",
            )

    return job_to_api_response(job, result=result)


@app.post("/api/upload")
async def upload_files(
    files: List[UploadFile] = File(default=[]),
    url: Optional[str] = Form(default=None),
):
    """Deprecated: submits a job and returns its initial status."""
    return await create_analysis_job(files=files, url=url)
