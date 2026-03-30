from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Job, JobData


async def create_job(db: AsyncSession, job_data: dict) -> Job:
    job = Job(**job_data)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_job_by_id(db: AsyncSession, job_id: UUID) -> Job | None:
    result = await db.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


async def get_jobs_by_user(db: AsyncSession, user_id: UUID) -> list[Job]:
    result = await db.execute(
        select(Job).where(Job.user_id == user_id).order_by(Job.created_at.desc())
    )
    return list(result.scalars().all())


async def update_job(db: AsyncSession, job_id: UUID, updates: dict) -> Job | None:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return None
    for field, value in updates.items():
        setattr(job, field, value)
    await db.commit()
    await db.refresh(job)
    return job


async def create_job_data(db: AsyncSession, job_id: UUID, row_index: int, data: dict) -> JobData:
    row = JobData(job_id=job_id, row_index=row_index, data=data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
