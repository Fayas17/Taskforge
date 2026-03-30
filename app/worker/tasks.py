import csv
from datetime import UTC, datetime
from uuid import UUID

import structlog
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.modules.auth.models import (
    User,  # noqa: F401 — registers auth.users in metadata for FK resolution
)
from app.modules.jobs.models import Job, JobData
from app.worker.celery_app import celery_app

logger = structlog.get_logger()

settings = get_settings()

# Sync engine — Celery cannot use asyncpg.
# psycopg2 is used here exclusively for the worker.
sync_engine = create_engine(settings.sync_database_url)
SyncSession = sessionmaker(bind=sync_engine, expire_on_commit=False)


def _get_job(db: Session, job_id: UUID) -> Job | None:
    return db.query(Job).filter(Job.id == job_id).first()


def _update_job(db: Session, job: Job, **fields: object) -> None:
    for key, value in fields.items():
        setattr(job, key, value)
    db.commit()


@celery_app.task(
    bind=True,
    max_retries=settings.CSV_TASK_MAX_RETRIES,
    default_retry_delay=settings.CSV_TASK_RETRY_DELAY,
    soft_time_limit=settings.CSV_TASK_SOFT_TIME_LIMIT,
    name="app.worker.tasks.process_csv",
)
def process_csv(self: Task, job_id: str) -> dict:
    """
    Process a CSV file asynchronously.

    Reads the file row by row, stores each row as a JobData record,
    and updates the Job's progress counters after every chunk.
    Supports resuming from the last committed checkpoint on retry —
    if a previous run committed 50k rows, the next attempt skips them
    and continues from row 50k.
    On per-row failure the row is skipped and appended to error_detail.
    On soft time limit the progress is saved and the task is retried.
    On unexpected exception the task is retried from the last checkpoint.
    """
    job_uuid = UUID(job_id)
    log = logger.bind(job_id=job_id, attempt=self.request.retries + 1)

    with SyncSession() as db:
        job = _get_job(db, job_uuid)
        if job is None:
            log.warning("csv_task_skipped", reason="job not found")
            return {"status": "skipped", "reason": "job not found"}

        if job.status == "completed":
            log.warning("csv_task_skipped", reason="already completed")
            return {"status": "skipped", "reason": "already completed"}

        # Resume from last checkpoint — rows already committed in a previous
        # attempt are skipped so we never duplicate job_data records.
        resume_from: int = job.processed_rows
        error_detail: list[dict] = list(job.error_detail) if job.error_detail else []
        processed: int = resume_from
        failed: int = job.failed_rows
        chunk: list[JobData] = []

        _update_job(db, job, status="processing")
        log.info("csv_task_started", file=job.file_name, resume_from=resume_from)

        try:
            with open(job.file_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            _update_job(db, job, total_rows=len(rows))
            log.info("csv_file_loaded", total_rows=len(rows))

            for index, row in enumerate(rows):
                # Skip rows already committed by a previous attempt.
                if index < resume_from:
                    continue

                try:
                    chunk.append(JobData(job_id=job_uuid, row_index=index, data=dict(row)))
                    processed += 1
                except Exception as row_err:
                    failed += 1
                    error_detail.append({"row": index, "error": str(row_err), "raw": dict(row)})
                    log.warning("csv_row_failed", row_index=index, error=str(row_err))

                if len(chunk) >= settings.CSV_CHUNK_SIZE:
                    db.bulk_save_objects(chunk)
                    db.commit()
                    chunk = []
                    # Save checkpoint — if we crash here, the next attempt
                    # resumes from this row instead of starting over.
                    _update_job(db, job, processed_rows=processed, failed_rows=failed)
                    log.info("csv_chunk_committed", processed=processed, failed=failed)

            # Flush remaining rows in the last partial chunk.
            if chunk:
                db.bulk_save_objects(chunk)
                db.commit()

            _update_job(
                db,
                job,
                status="completed",
                processed_rows=processed,
                failed_rows=failed,
                completed_at=datetime.now(UTC),
                result_data={
                    "total_rows": len(rows),
                    "processed_rows": processed,
                    "failed_rows": failed,
                    "success_rate": round((processed / len(rows)) * 100, 2) if rows else 0,
                },
                error_detail=error_detail if error_detail else None,
            )

            log.info("csv_task_completed", processed=processed, failed=failed)
            return {"status": "completed", "processed": processed, "failed": failed}

        except SoftTimeLimitExceeded:
            # The signal may have fired inside a flush, leaving the session in a
            # rolled-back state. Reset it before attempting any further DB work.
            db.rollback()
            # The in-flight chunk was not committed — don't count those rows.
            # processed already holds the last successfully committed checkpoint.
            _update_job(db, job, status="processing", processed_rows=processed, failed_rows=failed)
            log.warning("csv_task_soft_limit_hit", processed_rows=processed, retrying=True)
            # Retry — the next attempt will read processed_rows and skip ahead.
            raise self.retry(
                exc=SoftTimeLimitExceeded(),
                countdown=settings.CSV_TASK_RETRY_DELAY,
            ) from None

        except Exception as exc:
            # The exception may have fired inside a flush — rollback to reset the session.
            db.rollback()
            _update_job(db, job, status="processing", processed_rows=processed, failed_rows=failed)
            log.error("csv_task_failed", error=str(exc), processed_rows=processed)
            raise self.retry(exc=exc) from exc
