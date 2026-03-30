from pathlib import Path
from uuid import UUID

import structlog
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.jobs import repository
from app.modules.jobs.models import Job

settings = get_settings()
logger = structlog.get_logger()


async def upload_csv(db: AsyncSession, file: UploadFile, user_id: UUID) -> Job:
    logger.info("csv_upload_attempt", filename=file.filename, user_id=str(user_id))

    if not file.filename or not file.filename.endswith(".csv"):
        logger.warning("csv_upload_invalid_type", filename=file.filename, user_id=str(user_id))
        raise HTTPException(status_code=400, detail="Only CSV files are accepted")

    # Create a job record first to get the job ID for the file path.
    job = await repository.create_job(
        db,
        {
            "user_id": user_id,
            "status": "pending",
            "file_name": file.filename,
            "file_path": "",  # filled in after we know the job ID
        },
    )

    # Store file under uploads/{job_id}/filename — job-scoped so files never collide.
    upload_path = Path(settings.UPLOAD_DIR) / str(job.id)
    upload_path.mkdir(parents=True, exist_ok=True)
    file_path = upload_path / file.filename

    content = await file.read()
    file_path.write_bytes(content)

    # Update the job with the real file path.
    updated = await repository.update_job(db, job.id, {"file_path": str(file_path)})
    assert updated is not None
    job = updated

    # Dispatch the Celery task — import here to avoid circular imports at module load.
    from app.worker.tasks import process_csv

    task = process_csv.delay(str(job.id))

    # Store the Celery task ID so we can look it up or revoke it later.
    updated = await repository.update_job(db, job.id, {"celery_task_id": task.id})
    assert updated is not None
    job = updated

    logger.info(
        "csv_upload_dispatched",
        job_id=str(job.id),
        celery_task_id=task.id,
        user_id=str(user_id),
    )

    return job


async def retry_job(db: AsyncSession, job_id: UUID, user_id: UUID) -> Job:
    job = await get_job_status(db, job_id, user_id)  # reuses 404 + 403 checks

    if job.status == "completed":
        logger.warning("csv_job_retry_rejected", job_id=str(job_id), reason="already completed")
        raise HTTPException(status_code=400, detail="Job already completed — nothing to retry")

    if job.status == "pending":
        logger.warning("csv_job_retry_rejected", job_id=str(job_id), reason="still pending")
        raise HTTPException(
            status_code=400, detail="Job is still pending, not yet picked up by worker"
        )

    from app.worker.tasks import process_csv

    task = process_csv.delay(str(job.id))

    # Reset to pending so the status reflects it's back in the queue.
    updated = await repository.update_job(
        db, job.id, {"status": "pending", "celery_task_id": task.id}
    )
    assert updated is not None
    job = updated

    logger.info(
        "csv_job_retried",
        job_id=str(job_id),
        celery_task_id=task.id,
        user_id=str(user_id),
        resume_from=job.processed_rows,
    )
    return job


async def get_job_status(db: AsyncSession, job_id: UUID, user_id: UUID) -> Job:
    job = await repository.get_job_by_id(db, job_id)

    if job is None:
        logger.warning("csv_job_not_found", job_id=str(job_id), user_id=str(user_id))
        raise HTTPException(status_code=404, detail="Job not found")

    # Prevent users from accessing each other's jobs.
    if job.user_id != user_id:
        logger.warning(
            "csv_job_unauthorized",
            job_id=str(job_id),
            requesting_user=str(user_id),
            owner=str(job.user_id),
        )
        raise HTTPException(status_code=403, detail="Access denied")

    return job
