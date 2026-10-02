"""
Scenes API (Phase 4: script -> scenes). Lets the scene breakdown produced by
Phase 3 be listed, reviewed, edited one at a time, and finalized before
Phase 5 (images) and Phase 6 (voice) start consuming it.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.project import ProjectResponse
from app.schemas.scene import SceneResponse, SceneUpdate
from app.services import scene_service
from app.services.render_service import render_scene_clip
from app.services.tts_service import generate_scene_audio
from app.services.visual_service import generate_scene_image

router = APIRouter(prefix="/api/projects/{project_id}/scenes", tags=["scenes"])


@router.get("", response_model=List[SceneResponse])
def list_project_scenes(project_id: uuid.UUID, db: Session = Depends(get_db)) -> List[SceneResponse]:
    """List a project's scenes, ordered by scene_number."""
    return scene_service.list_scenes(project_id, db)


@router.get("/{scene_id}", response_model=SceneResponse)
def get_project_scene(
    project_id: uuid.UUID, scene_id: uuid.UUID, db: Session = Depends(get_db)
) -> SceneResponse:
    return scene_service.get_scene(project_id, scene_id, db)


@router.patch("/{scene_id}", response_model=SceneResponse)
def update_project_scene(
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
    payload: SceneUpdate,
    db: Session = Depends(get_db),
) -> SceneResponse:
    """
    Manually edit narration / visual_prompt / on_screen_text / duration /
    transition on one scene before finalizing. Only the fields included in
    the request body are changed.
    """
    return scene_service.update_scene(project_id, scene_id, payload, db)


@router.post("/finalize", response_model=ProjectResponse, status_code=status.HTTP_200_OK)
def finalize_project_scenes(project_id: uuid.UUID, db: Session = Depends(get_db)) -> ProjectResponse:
    """
    Validate and lock in the scene breakdown, moving the project from
    SCRIPT_GENERATED to SCENES_GENERATED.
    """
    return scene_service.finalize_scenes(project_id, db)


@router.post("/{scene_id}/generate-image", response_model=SceneResponse)
async def generate_scene_image_endpoint(
    project_id: uuid.UUID, scene_id: uuid.UUID, db: Session = Depends(get_db)
) -> SceneResponse:
    """
    Phase 5: (re)generate the image for exactly one scene, via the
    configured VISUAL_PROVIDER. Useful after editing a scene's
    visual_prompt (which resets it to PENDING) without re-running the
    whole project's image generation.
    """
    return await generate_scene_image(project_id, scene_id, db)


@router.post("/{scene_id}/generate-audio", response_model=SceneResponse)
async def generate_scene_audio_endpoint(
    project_id: uuid.UUID, scene_id: uuid.UUID, db: Session = Depends(get_db)
) -> SceneResponse:
    """
    Phase 6: (re)generate the narration audio for exactly one scene, via
    the configured TTS_PROVIDER. Useful after editing a scene's narration
    (which resets it to PENDING) without re-running the whole project's
    audio generation.
    """
    return await generate_scene_audio(project_id, scene_id, db)


@router.post("/{scene_id}/render-clip", response_model=SceneResponse)
async def render_scene_clip_endpoint(
    project_id: uuid.UUID, scene_id: uuid.UUID, db: Session = Depends(get_db)
) -> SceneResponse:
    """
    Phase 7: (re)render exactly one scene's video clip. Requires the scene
    to already have both an image and audio.
    """
    return await render_scene_clip(project_id, scene_id, db)
