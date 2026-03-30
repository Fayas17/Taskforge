from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limiter import limiter
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.jobs import service
from app.modules.jobs.schemas import JobResponse, JobStatusResponse

router = APIRouter()
settings = get_settings()
logger = structlog.get_logger()


@router.post("/upload/", response_model=JobResponse)
@limiter.limit(settings.RATE_LIMIT_JOBS_UPLOAD)
async def upload_csv(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    ip = request.client.host if request.client else "unknown"
    logger.info(
        "jobs_upload_endpoint_called",
        filename=file.filename,
        user_id=str(current_user.id),
        ip_address=ip,
    )

    job = await service.upload_csv(db, file, current_user.id)

    logger.info(
        "jobs_upload_endpoint_success",
        job_id=str(job.id),
        user_id=str(current_user.id),
        ip_address=ip,
    )
    return job


@router.get("/", response_model=list[JobResponse])
async def list_jobs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    from app.modules.jobs import repository

    ip = request.client.host if request.client else "unknown"
    logger.info("jobs_list_endpoint_called", user_id=str(current_user.id), ip_address=ip)

    jobs = await repository.get_jobs_by_user(db, current_user.id)
    return jobs


@router.post("/{job_id}/retry/", response_model=JobResponse)
async def retry_job(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    ip = request.client.host if request.client else "unknown"
    logger.info(
        "jobs_retry_endpoint_called",
        job_id=str(job_id),
        user_id=str(current_user.id),
        ip_address=ip,
    )
    return await service.retry_job(db, job_id, current_user.id)


@router.get("/{job_id}/", response_model=JobStatusResponse)
async def get_job_status(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    ip = request.client.host if request.client else "unknown"
    logger.info(
        "jobs_status_endpoint_called",
        job_id=str(job_id),
        user_id=str(current_user.id),
        ip_address=ip,
    )

    job = await service.get_job_status(db, job_id, current_user.id)

    logger.info(
        "jobs_status_endpoint_success",
        job_id=str(job_id),
        status=job.status,
        user_id=str(current_user.id),
    )
    return job
