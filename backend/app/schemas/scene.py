import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import JobStatus


class SceneUpdate(BaseModel):
    """
    Phase 4: manual edits to a single scene before it's finalized and handed
    to Phase 5 (images) / Phase 6 (voice). Every field is optional — only
    the fields actually sent are changed (see scene_service.update_scene).
    """

    narration: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    visual_prompt: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    on_screen_text: Optional[str] = Field(default=None, max_length=120)
    duration_seconds: Optional[int] = Field(default=None, ge=3, le=120)
    transition: Optional[str] = Field(default=None, max_length=32)


class SceneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    scene_number: int
    duration_seconds: Optional[int] = None
    narration: Optional[str] = None
    visual_prompt: Optional[str] = None
    on_screen_text: Optional[str] = None
    transition: Optional[str] = None
    image_path: Optional[str] = None
    audio_path: Optional[str] = None
    scene_video_path: Optional[str] = None
    status: JobStatus
    attempt_count: int
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
