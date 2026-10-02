"""
Phase 4: script -> scenes.

Phase 3 already persists the raw scene breakdown the LLM returned (narration,
visual prompt, on-screen text, duration, transition per scene). This service
is the layer on top of that: it lets those scenes be listed and reviewed,
edited one at a time (the "approval before rendering" workflow from the
spec), and then finalized — validated and locked in — so the project moves
from SCRIPT_GENERATED to SCENES_GENERATED and Phase 5 (images) / Phase 6
(voice) know the scene breakdown is safe to start consuming.
"""

import uuid
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.base import JobStatus, LogLevel, ProjectStatus
from app.models.generation_log import GenerationLog
from app.models.project import Project
from app.models.scene import Scene
from app.schemas.scene import SceneUpdate
from app.utils.logger import get_logger

log = get_logger(__name__)

# The target duration from Phase 3 is a strong hint to the LLM, not a hard
# guarantee (section 4). Finalization is refused if the scenes drift from it
# by more than this many seconds, to catch a badly-generated script early
# rather than silently rendering a video far from the requested length.
DURATION_TOLERANCE_SECONDS = 30


def _get_project_or_404(project_id: uuid.UUID, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _get_scene_or_404(project_id: uuid.UUID, scene_id: uuid.UUID, db: Session) -> Scene:
    scene = db.get(Scene, scene_id)
    if scene is None or scene.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found")
    return scene


def list_scenes(project_id: uuid.UUID, db: Session) -> List[Scene]:
    project = _get_project_or_404(project_id, db)
    return sorted(project.scenes, key=lambda s: s.scene_number)


def get_scene(project_id: uuid.UUID, scene_id: uuid.UUID, db: Session) -> Scene:
    _get_project_or_404(project_id, db)
    return _get_scene_or_404(project_id, scene_id, db)


def update_scene(project_id: uuid.UUID, scene_id: uuid.UUID, payload: SceneUpdate, db: Session) -> Scene:
    """
    Apply a partial edit to one scene. Editing any content field (narration,
    visual prompt, on-screen text, duration) resets that scene's own
    pipeline status back to PENDING and clears any previously generated
    image/audio/video paths, since those would now be stale — this has no
    visible effect yet (Phase 5/6 don't exist), but keeps every edited scene
    in a clean PENDING state ready for when they do.
    """
    _get_project_or_404(project_id, db)
    scene = _get_scene_or_404(project_id, scene_id, db)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No fields to update")

    content_fields = {"narration", "visual_prompt", "on_screen_text", "duration_seconds"}
    touched_content = bool(content_fields & updates.keys())

    for field, value in updates.items():
        setattr(scene, field, value)

    if touched_content:
        scene.status = JobStatus.PENDING
        scene.image_path = None
        scene.audio_path = None
        scene.scene_video_path = None
        scene.attempt_count = 0
        scene.last_error = None

    db.add(
        GenerationLog(
            project_id=project_id,
            scene_id=scene.id,
            stage="scene_edit",
            level=LogLevel.INFO,
            message=f"Scene {scene.scene_number} manually edited: {sorted(updates.keys())}",
        )
    )
    db.commit()
    db.refresh(scene)
    log.info(
        "scene_updated",
        project_id=str(project_id),
        scene_id=str(scene.id),
        fields=sorted(updates.keys()),
    )
    return scene


def finalize_scenes(project_id: uuid.UUID, db: Session) -> Project:
    """
    Validate the full scene breakdown and lock it in:
      - every scene must have narration, a visual prompt and a duration
      - the total duration must be within tolerance of the project's target
        (or, if no target was set at creation, the total becomes the target)
    On success the project moves to SCENES_GENERATED. Safe to call again
    after further edits (idempotent re-validation).
    """
    project = _get_project_or_404(project_id, db)

    if project.status not in (ProjectStatus.SCRIPT_GENERATED, ProjectStatus.SCENES_GENERATED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Project must have a generated script before finalizing scenes "
                f"(current status: {project.status.value})"
            ),
        )

    scenes = sorted(project.scenes, key=lambda s: s.scene_number)
    if not scenes:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project has no scenes to finalize")

    missing = [
        s.scene_number
        for s in scenes
        if not s.narration or not s.visual_prompt or not s.duration_seconds
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Scenes missing narration/visual_prompt/duration: {missing}",
        )

    total_duration = sum(s.duration_seconds for s in scenes)
    if project.duration_seconds:
        drift = abs(total_duration - project.duration_seconds)
        if drift > DURATION_TOLERANCE_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Total scene duration ({total_duration}s) drifts too far from the "
                    f"project's target ({project.duration_seconds}s). Edit scene durations "
                    "via PATCH before finalizing."
                ),
            )
    else:
        project.duration_seconds = total_duration

    project.status = ProjectStatus.SCENES_GENERATED
    db.add(
        GenerationLog(
            project_id=project_id,
            stage="scenes_finalized",
            level=LogLevel.INFO,
            message=f"{len(scenes)} scenes finalized, total duration {total_duration}s",
        )
    )
    db.commit()
    db.refresh(project)
    log.info(
        "scenes_finalized",
        project_id=str(project_id),
        scene_count=len(scenes),
        total_duration=total_duration,
    )
    return project
