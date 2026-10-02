"""
Pydantic schemas for the Project resource — the request/response contract
for the API, kept separate from the SQLAlchemy ORM model (app/models/project.py)
so DB structure and API shape can evolve independently.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import ProjectStatus
from app.schemas.scene import SceneResponse


class ProjectCreate(BaseModel):
    """Body for POST /api/projects — the only required field is the prompt."""

    user_prompt: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="The user's video request, e.g. 'Create a 5-minute video explaining how robots use sensors.'",
    )
    language: str = Field(default="en-US", description="BCP-47 locale, e.g. 'en-US', 'hi-IN'.")
    target_audience: Optional[str] = Field(default=None, max_length=255)
    duration_seconds: Optional[int] = Field(
        default=None, ge=30, le=1800, description="Desired video length in seconds, 30s to 30min."
    )


class ProjectResponse(BaseModel):
    """Full project representation returned by GET/POST endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_prompt: str
    title: Optional[str] = None
    description: Optional[str] = None
    status: ProjectStatus
    language: str
    target_audience: Optional[str] = None
    duration_seconds: Optional[int] = None
    video_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    captions_path: Optional[str] = None
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    youtube_title: Optional[str] = None
    youtube_description: Optional[str] = None
    youtube_tags: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    scenes: List[SceneResponse] = []


class ProjectStatusResponse(BaseModel):
    """Lightweight shape for GET /api/projects/{id}/status — for polling."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ProjectStatus
    last_error: Optional[str] = None
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int
