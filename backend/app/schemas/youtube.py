"""Pydantic schemas for the YouTube OAuth (Phase 12) and upload (Phase 13) endpoints."""

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.models.base import JobStatus


class YouTubeConnectionStatus(BaseModel):
    connected: bool
    channel_id: Optional[str] = None
    channel_title: Optional[str] = None
    token_expiry: Optional[datetime] = None


class YouTubeUploadRequest(BaseModel):
    """Optional body for POST .../youtube/upload. Omit to use YOUTUBE_DEFAULT_PRIVACY_STATUS."""

    privacy_status: Optional[Literal["private", "unlisted", "public"]] = None


class YouTubeUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    privacy_status: str
    status: JobStatus
    attempt_count: int
    last_error: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
