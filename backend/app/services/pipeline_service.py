"""
Small shared helper for enqueueing the Phase 11 full-pipeline Celery task.

Extracted out of app/api/projects.py's POST .../pipeline endpoint so Phase
14's n8n trigger endpoint (app/api/webhooks.py) can start the exact same
background run from a project it just created, without duplicating the Job
bookkeeping.
"""

import uuid

from sqlalchemy.orm import Session

from app.models.base import JobStatus, JobType
from app.models.job import Job


def enqueue_full_pipeline(project_id: uuid.UUID, db: Session) -> Job:
    """Creates a Job row and hands it to Celery. Does not check project state —
    callers that need a project to exist/be valid should do that first."""

    # Local import: only the worker process needs Celery to actually be
    # configured against a reachable broker; importing it here (rather than
    # at module load) keeps that failure scoped to callers that need it.
    from app.workers.tasks import run_full_pipeline_task

    job = Job(project_id=project_id, job_type=JobType.FULL_PIPELINE, status=JobStatus.PENDING)
    db.add(job)
    db.flush()  # populate job.id before using it in the task payload

    result = run_full_pipeline_task.delay(str(project_id), str(job.id))
    job.celery_task_id = result.id
    db.commit()
    db.refresh(job)
    return job
