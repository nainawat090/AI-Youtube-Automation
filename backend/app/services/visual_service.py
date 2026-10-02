"""
Phase 5: scene -> images.

Turns each finalized scene's visual_prompt into an actual image file on
disk, via whichever VisualProvider is configured, and records it both on
the Scene row (image_path, status) and as an Asset row (the durable,
independently-queryable record of "this file belongs to this scene",
per section 22). A project only reaches VISUALS_GENERATED once every one
of its scenes has a successful image.
"""

import os
import uuid
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.asset import Asset
from app.models.base import AssetType, JobStatus, LogLevel, ProjectStatus
from app.models.generation_log import GenerationLog
from app.models.project import Project
from app.models.scene import Scene
from app.services.visual_provider import VisualProviderError, get_visual_provider
from app.utils.logger import get_logger

log = get_logger(__name__)


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


def _image_output_path(project_id: uuid.UUID, scene: Scene) -> str:
    directory = os.path.join(settings.local_storage_path, "images", str(project_id))
    return os.path.join(directory, f"scene_{scene.scene_number:02d}.png")


async def generate_scene_image(project_id: uuid.UUID, scene_id: uuid.UUID, db: Session) -> Scene:
    """
    Generate (or regenerate) the image for exactly one scene. Safe to call
    on a scene that already has an image — it will be overwritten and the
    Asset row updated in place.
    """
    _get_project_or_404(project_id, db)
    scene = _get_scene_or_404(project_id, scene_id, db)

    if not scene.visual_prompt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Scene {scene.scene_number} has no visual_prompt to generate an image from",
        )

    output_path = _image_output_path(project_id, scene)

    scene.status = JobStatus.RUNNING
    db.commit()

    try:
        provider = get_visual_provider()
        await provider.generate_image(scene.visual_prompt, output_path)
    except VisualProviderError as exc:
        scene.status = JobStatus.FAILED
        scene.attempt_count += 1
        scene.last_error = str(exc)
        db.add(
            GenerationLog(
                project_id=project_id,
                scene_id=scene.id,
                stage="image_generation",
                level=LogLevel.ERROR,
                message=f"Image generation failed for scene {scene.scene_number}",
                error_details=str(exc),
            )
        )
        db.commit()
        log.error(
            "scene_image_generation_failed",
            project_id=str(project_id),
            scene_id=str(scene.id),
            error=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    scene.image_path = output_path
    scene.status = JobStatus.SUCCEEDED
    scene.attempt_count += 1
    scene.last_error = None

    existing_asset = next(
        (a for a in scene.project.assets if a.scene_id == scene.id and a.asset_type == AssetType.IMAGE),
        None,
    )
    if existing_asset:
        existing_asset.file_path = output_path
        existing_asset.provider = settings.visual_provider
        existing_asset.prompt_used = scene.visual_prompt
    else:
        db.add(
            Asset(
                project_id=project_id,
                scene_id=scene.id,
                asset_type=AssetType.IMAGE,
                file_path=output_path,
                provider=settings.visual_provider,
                prompt_used=scene.visual_prompt,
            )
        )

    db.add(
        GenerationLog(
            project_id=project_id,
            scene_id=scene.id,
            stage="image_generation",
            level=LogLevel.INFO,
            message=f"Image generated for scene {scene.scene_number} via {settings.visual_provider}",
        )
    )
    db.commit()
    db.refresh(scene)
    log.info(
        "scene_image_generated",
        project_id=str(project_id),
        scene_id=str(scene.id),
        path=output_path,
    )
    return scene


async def generate_project_images(project_id: uuid.UUID, db: Session) -> Project:
    """
    Generate images for every scene in a project that doesn't already have
    a successful one. Requires the project to have finalized scenes
    (SCENES_GENERATED) or to already be mid-way through images
    (VISUALS_GENERATED, for a retry after a partial failure). Moves the
    project to VISUALS_GENERATED once every scene succeeds; leaves it at
    SCENES_GENERATED (so this endpoint can simply be called again) if any
    scene still failed after this pass.
    """
    project = _get_project_or_404(project_id, db)

    if project.status not in (ProjectStatus.SCENES_GENERATED, ProjectStatus.VISUALS_GENERATED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Project must have finalized scenes before generating images "
                f"(current status: {project.status.value})"
            ),
        )

    scenes: List[Scene] = sorted(project.scenes, key=lambda s: s.scene_number)
    if not scenes:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project has no scenes")

    pending_scenes = [s for s in scenes if s.image_path is None]
    failures = []

    for scene in pending_scenes:
        try:
            await generate_scene_image(project_id, scene.id, db)
        except HTTPException as exc:
            failures.append({"scene_number": scene.scene_number, "error": exc.detail})

    db.refresh(project)
    all_succeeded = all(s.image_path is not None for s in project.scenes)

    if all_succeeded:
        project.status = ProjectStatus.VISUALS_GENERATED
        db.add(
            GenerationLog(
                project_id=project_id,
                stage="images_completed",
                level=LogLevel.INFO,
                message=f"All {len(scenes)} scene images generated",
            )
        )
        db.commit()
        db.refresh(project)
        log.info("project_images_completed", project_id=str(project_id), scene_count=len(scenes))
        return project

    # Not every scene has a successful image right now — the project must
    # not claim VISUALS_GENERATED (that status is an invariant: it only
    # holds when every scene has an image). This also covers the retry
    # case, where a previously-completed project had a scene edited and
    # its regeneration then failed again.
    project.status = ProjectStatus.SCENES_GENERATED
    db.add(
        GenerationLog(
            project_id=project_id,
            stage="images_partial_failure",
            level=LogLevel.ERROR,
            message=f"{len(failures)} of {len(scenes)} scenes failed image generation",
            error_details=str(failures),
        )
    )
    db.commit()
    log.error(
        "project_images_partial_failure",
        project_id=str(project_id),
        failures=failures,
    )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "message": "Some scenes failed image generation. Fix the underlying issue and call this endpoint again — already-successful scenes are skipped.",
            "failures": failures,
        },
    )
