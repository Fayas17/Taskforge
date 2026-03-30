from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobResponse(BaseModel):
    """Returned immediately after a CSV is uploaded — gives the user their job_id."""

    id: UUID
    status: str
    file_name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobStatusResponse(BaseModel):
    """Returned when the user polls GET /jobs/{id}/ — full progress detail."""

    id: UUID
    status: str
    file_name: str
    total_rows: int
    processed_rows: int
    failed_rows: int
    result_data: dict | None
    error_detail: dict | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
