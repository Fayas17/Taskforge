import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__: ClassVar[dict] = {"schema": "job"}

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    processed_rows: Mapped[int] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)
    result_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rows: Mapped[list["JobData"]] = relationship("JobData", back_populates="job")


class JobData(Base):
    __tablename__ = "job_data"
    __table_args__: ClassVar[dict] = {"schema": "job"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("job.jobs.id"), nullable=False
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    job: Mapped["Job"] = relationship("Job", back_populates="rows")
