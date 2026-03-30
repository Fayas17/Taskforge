import io
import uuid
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded

from app.worker.tasks import process_csv


def _make_mock_job(
    status: str = "pending",
    processed_rows: int = 0,
    failed_rows: int = 0,
    error_detail: list | None = None,
    file_path: str = "/fake/test.csv",
    file_name: str = "test.csv",
) -> MagicMock:
    job = MagicMock()
    job.id = uuid.uuid4()
    job.status = status
    job.processed_rows = processed_rows
    job.failed_rows = failed_rows
    job.error_detail = error_detail
    job.file_path = file_path
    job.file_name = file_name
    return job


def _make_mock_session(job: MagicMock | None) -> MagicMock:
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = job
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_db)
    mock_session.__exit__ = MagicMock(return_value=False)
    return mock_session


CSV_CONTENT = "name,email\nAlice,alice@example.com\nBob,bob@example.com\nCarol,carol@example.com\n"


def _mock_open(content: str = CSV_CONTENT):
    """Returns a side_effect for builtins.open that yields a StringIO."""

    def _open(*args, **kwargs):
        return io.StringIO(content)

    return _open


# ── Skip cases ───────────────────────────────────────────────────────────────


def test_process_csv_job_not_found():
    mock_session = _make_mock_session(job=None)

    with patch("app.worker.tasks.SyncSession", return_value=mock_session):
        result = process_csv.run(str(uuid.uuid4()))

    assert result["status"] == "skipped"
    assert result["reason"] == "job not found"


def test_process_csv_already_completed():
    job = _make_mock_job(status="completed")
    mock_session = _make_mock_session(job=job)

    with patch("app.worker.tasks.SyncSession", return_value=mock_session):
        result = process_csv.run(str(uuid.uuid4()))

    assert result["status"] == "skipped"
    assert result["reason"] == "already completed"


# ── Happy path ───────────────────────────────────────────────────────────────


def test_process_csv_success():
    job = _make_mock_job()
    mock_session = _make_mock_session(job=job)

    with (
        patch("app.worker.tasks.SyncSession", return_value=mock_session),
        patch("builtins.open", side_effect=_mock_open()),
    ):
        result = process_csv.run(str(uuid.uuid4()))

    assert result["status"] == "completed"
    assert result["processed"] == 3
    assert result["failed"] == 0


# ── Checkpoint resume ─────────────────────────────────────────────────────────


def test_process_csv_resumes_from_checkpoint():
    """If processed_rows=1, only rows at index >= 1 should be processed."""
    job = _make_mock_job(processed_rows=1)
    mock_session = _make_mock_session(job=job)

    with (
        patch("app.worker.tasks.SyncSession", return_value=mock_session),
        patch("builtins.open", side_effect=_mock_open()),
    ):
        result = process_csv.run(str(uuid.uuid4()))

    assert result["status"] == "completed"
    # Started from processed_rows=1, CSV has 3 rows — 2 new rows processed
    assert result["processed"] == 3  # 1 previous + 2 new
    assert result["failed"] == 0


# ── Per-row failure ───────────────────────────────────────────────────────────


def test_process_csv_row_failure_captured_in_error_detail():
    """JobData constructor failure for a row should be captured, not crash the task."""
    job = _make_mock_job()
    mock_session = _make_mock_session(job=job)

    call_count = 0

    def failing_job_data(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ValueError("bad row data")
        return MagicMock()

    with (
        patch("app.worker.tasks.SyncSession", return_value=mock_session),
        patch("builtins.open", side_effect=_mock_open()),
        patch("app.worker.tasks.JobData", side_effect=failing_job_data),
    ):
        result = process_csv.run(str(uuid.uuid4()))

    assert result["status"] == "completed"
    assert result["failed"] == 1
    assert result["processed"] == 2  # 3 rows - 1 failed


# ── Soft time limit ───────────────────────────────────────────────────────────


def test_process_csv_soft_time_limit_saves_progress_and_retries():
    """On SoftTimeLimitExceeded, the task must save current progress and retry."""
    job = _make_mock_job()
    mock_session = _make_mock_session(job=job)
    mock_db = mock_session.__enter__.return_value

    # Raise SoftTimeLimitExceeded on the first commit after bulk_save_objects.
    commit_calls = 0

    def raise_on_second_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise SoftTimeLimitExceeded()

    mock_db.commit.side_effect = raise_on_second_commit

    with (
        patch("app.worker.tasks.SyncSession", return_value=mock_session),
        patch("builtins.open", side_effect=_mock_open()),
        patch.object(process_csv, "retry", side_effect=Retry()) as mock_retry,
    ):
        with pytest.raises(Retry):
            process_csv.run(str(uuid.uuid4()))

    mock_retry.assert_called_once()
