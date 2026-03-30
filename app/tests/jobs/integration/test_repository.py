import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth import repository as auth_repo
from app.modules.jobs import repository as jobs_repo

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def user(db: AsyncSession):
    """Creates a real user in the test DB for FK constraints on job.user_id."""
    return await auth_repo.create_user(
        db,
        {
            "email": f"{uuid.uuid4()}@example.com",
            "username": f"user_{uuid.uuid4().hex[:8]}",
            "hashed_password": "hashed",
        },
    )


@pytest_asyncio.fixture()
async def job(db: AsyncSession, user):
    """Creates a job owned by the test user."""
    return await jobs_repo.create_job(
        db,
        {
            "user_id": user.id,
            "status": "pending",
            "file_name": "test.csv",
            "file_path": "/uploads/test.csv",
        },
    )


# ── create_job / get_job_by_id ────────────────────────────────────────────────


async def test_create_and_get_job(db: AsyncSession, user):
    created = await jobs_repo.create_job(
        db,
        {
            "user_id": user.id,
            "status": "pending",
            "file_name": "data.csv",
            "file_path": "/uploads/data.csv",
        },
    )
    assert created.id is not None
    assert created.status == "pending"
    assert created.file_name == "data.csv"

    fetched = await jobs_repo.get_job_by_id(db, created.id)
    assert fetched is not None
    assert fetched.id == created.id


async def test_get_job_by_id_not_found(db: AsyncSession):
    result = await jobs_repo.get_job_by_id(db, uuid.uuid4())
    assert result is None


# ── update_job ────────────────────────────────────────────────────────────────


async def test_update_job_status(db: AsyncSession, job):
    updated = await jobs_repo.update_job(db, job.id, {"status": "processing", "processed_rows": 50})
    assert updated is not None
    assert updated.status == "processing"
    assert updated.processed_rows == 50


async def test_update_job_not_found(db: AsyncSession):
    result = await jobs_repo.update_job(db, uuid.uuid4(), {"status": "completed"})
    assert result is None


# ── get_jobs_by_user ──────────────────────────────────────────────────────────


async def test_get_jobs_by_user_returns_only_own_jobs(db: AsyncSession, user):
    other_user = await auth_repo.create_user(
        db,
        {
            "email": f"{uuid.uuid4()}@example.com",
            "username": f"user_{uuid.uuid4().hex[:8]}",
            "hashed_password": "hashed",
        },
    )

    await jobs_repo.create_job(
        db,
        {"user_id": user.id, "status": "pending", "file_name": "a.csv", "file_path": "/a.csv"},
    )
    await jobs_repo.create_job(
        db,
        {"user_id": user.id, "status": "pending", "file_name": "b.csv", "file_path": "/b.csv"},
    )
    await jobs_repo.create_job(
        db,
        {
            "user_id": other_user.id,
            "status": "pending",
            "file_name": "c.csv",
            "file_path": "/c.csv",
        },
    )

    user_jobs = await jobs_repo.get_jobs_by_user(db, user.id)
    assert len(user_jobs) == 2
    assert all(j.user_id == user.id for j in user_jobs)


# ── create_job_data ───────────────────────────────────────────────────────────


async def test_create_job_data(db: AsyncSession, job):
    row_data = {"name": "Alice", "email": "alice@example.com"}
    row = await jobs_repo.create_job_data(db, job.id, row_index=0, data=row_data)

    assert row.id is not None
    assert row.job_id == job.id
    assert row.row_index == 0
    assert row.data == row_data
