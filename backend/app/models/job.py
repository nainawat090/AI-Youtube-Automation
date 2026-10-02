"""
Job: tracks a background task (Celery) run against a project — the full
pipeline run, a single scene regeneration, a render, or a YouTube upload.
Separate from Project.status so we keep a full history of attempts rather
than overwriting state (section 18/21).
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import JobStatus, JobType, TimestampMixin, UUIDPKMixin


class Job(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "jobs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    job_type: Mapped[JobType] = mapped_column(
        SAEnum(JobType, name="job_type", native_enum=False, length=32), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status", native_enum=False, length=32),
        default=JobStatus.PENDING,
        nullable=False,
        index=True,
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # Retry tracking (section 21)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="jobs")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job type={self.job_type} status={self.status}>"
