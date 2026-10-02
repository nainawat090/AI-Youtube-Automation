"""
Phase 11: Celery application.

Wraps the existing pipeline service functions (script/images/audio/render/
captions/thumbnail — Phases 3-9) so the full prompt-to-video pipeline can
run in a background worker process instead of blocking an HTTP request. A
full run can easily take several minutes (image generation + narration +
ffmpeg rendering + caption burn-in), far longer than is reasonable to hold
an HTTP connection open for.

This does NOT replace the synchronous per-stage endpoints from Phases 3-9
— those still work exactly as before, are still the right tool for "just
this one stage, and I want the result immediately", and are what the
Celery task in app/workers/tasks.py calls under the hood. This only adds a
non-blocking way to run all of them in sequence.

Run the worker with:
    celery -A app.workers.celery_app worker --loglevel=info
(the `worker` service in docker-compose.yml already does this).
"""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "ai_youtube_automation",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    # A full pipeline run (script + images + audio + render + captions +
    # thumbnail) can take several minutes. This must comfortably exceed the
    # longest realistic run, or Redis considers the task lost mid-run and
    # redelivers it to another worker, causing it to run twice.
    broker_transport_options={"visibility_timeout": 3600},
    result_expires=86400,
)

# Celery only registers tasks defined in modules it has imported — this
# import is what makes `celery -A app.workers.celery_app worker` find them.
from app.workers import tasks  # noqa: E402,F401
