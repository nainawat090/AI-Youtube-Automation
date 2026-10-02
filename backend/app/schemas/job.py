"""
Pydantic schema for the Job resource (Phase 11) — the response shape for
POST .../pipeline and the job-polling endpoints.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.base import JobStatus, JobType


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    job_type: JobType
    status: JobStatus
    celery_task_id: Optional[str] = None
    attempt_count: int
    last_error: Optional[str] = None
    last_attempt_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
