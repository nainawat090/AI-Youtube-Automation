"""
Shared building blocks for ORM models: a UUID primary key mixin, a
created_at/updated_at timestamp mixin, and the pipeline status enum used
by Project (and referenced by jobs/scenes for finer-grained state).
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UUIDPKMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ProjectStatus(str, enum.Enum):
    """Pipeline stage status, per PROJECT.md section 5 + section 16 upload workflow."""
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    SCRIPT_GENERATED = "SCRIPT_GENERATED"
    SCENES_GENERATED = "SCENES_GENERATED"
    VISUALS_GENERATED = "VISUALS_GENERATED"
    VOICE_GENERATED = "VOICE_GENERATED"
    CAPTIONS_GENERATED = "CAPTIONS_GENERATED"
    RENDERING = "RENDERING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    UPLOAD_STARTED = "UPLOAD_STARTED"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class AssetType(str, enum.Enum):
    IMAGE = "IMAGE"
    AUDIO_NARRATION = "AUDIO_NARRATION"
    MUSIC = "MUSIC"
    CAPTION_SRT = "CAPTION_SRT"
    CAPTION_VTT = "CAPTION_VTT"
    SCENE_VIDEO = "SCENE_VIDEO"
    FINAL_VIDEO = "FINAL_VIDEO"
    THUMBNAIL = "THUMBNAIL"


class JobType(str, enum.Enum):
    FULL_PIPELINE = "FULL_PIPELINE"
    SCRIPT_GENERATION = "SCRIPT_GENERATION"
    SCENE_REGENERATION = "SCENE_REGENERATION"
    RENDER = "RENDER"
    YOUTUBE_UPLOAD = "YOUTUBE_UPLOAD"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class LogLevel(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
