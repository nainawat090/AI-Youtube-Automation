"""
Jobs API (Phase 11). Standalone lookup for a background job by id — the
project-scoped list lives at GET /api/projects/{project_id}/jobs in
app/api/projects.py, alongside where the job gets created
(POST /api/projects/{project_id}/pipeline).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.job import Job
from app.schemas.job import JobResponse

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> Job:
    """Poll a background job's status (e.g. the one POST .../pipeline returned)."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
