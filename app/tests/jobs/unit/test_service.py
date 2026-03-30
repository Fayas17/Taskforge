import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.modules.jobs.service import get_job_status, upload_csv

# ── upload_csv validation ────────────────────────────────────────────────────


async def test_upload_csv_rejects_non_csv_extension():
    mock_file = MagicMock()
    mock_file.filename = "data.txt"

    with pytest.raises(HTTPException) as exc_info:
        await upload_csv(db=AsyncMock(), file=mock_file, user_id=uuid.uuid4())

    assert exc_info.value.status_code == 400


async def test_upload_csv_rejects_no_filename():
    mock_file = MagicMock()
    mock_file.filename = None

    with pytest.raises(HTTPException) as exc_info:
        await upload_csv(db=AsyncMock(), file=mock_file, user_id=uuid.uuid4())

    assert exc_info.value.status_code == 400


# ── get_job_status ───────────────────────────────────────────────────────────


async def test_get_job_status_not_found():
    with patch(
        "app.modules.jobs.service.repository.get_job_by_id", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_job_status(db=AsyncMock(), job_id=uuid.uuid4(), user_id=uuid.uuid4())

    assert exc_info.value.status_code == 404


async def test_get_job_status_unauthorized():
    """A user requesting a job owned by another user gets 403."""
    owner_id = uuid.uuid4()
    requester_id = uuid.uuid4()

    mock_job = MagicMock()
    mock_job.user_id = owner_id

    with patch(
        "app.modules.jobs.service.repository.get_job_by_id", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = mock_job

        with pytest.raises(HTTPException) as exc_info:
            await get_job_status(db=AsyncMock(), job_id=uuid.uuid4(), user_id=requester_id)

    assert exc_info.value.status_code == 403
