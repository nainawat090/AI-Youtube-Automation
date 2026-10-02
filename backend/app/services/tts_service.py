"""
Phase 6: scene -> voice narration.

Turns each scene's narration text into a spoken-audio file via whichever
TTSProvider is configured, recording it on the Scene row (audio_path) and
as an Asset row (AUDIO_NARRATION), mirroring visual_service.py for images.
A project only reaches VOICE_GENERATED once every one of its scenes has
narration audio.

Completion is judged by `scene.audio_path is not None` rather than the
generic `scene.status` field, on purpose: status is shared across every
per-scene generation step (image, audio, and eventually the rendered scene
clip), so "am I done with audio" has to be its own check independent of
whatever image generation last left status as.
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
from app.services.tts_provider import TTSProviderError, get_tts_provider
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


def _audio_output_path(project_id: uuid.UUID, scene: Scene) -> str:
    directory = os.path.join(settings.local_storage_path, "audio", str(project_id))
    return os.path.join(directory, f"scene_{scene.scene_number:02d}.mp3")


async def generate_scene_audio(project_id: uuid.UUID, scene_id: uuid.UUID, db: Session) -> Scene:
    """
    Generate (or regenerate) the narration audio for exactly one scene.
    Safe to call on a scene that already has audio — it will be
    overwritten and the Asset row updated in place.
    """
    project = _get_project_or_404(project_id, db)
    scene = _get_scene_or_404(project_id, scene_id, db)

    if not scene.narration:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Scene {scene.scene_number} has no narration to synthesize",
        )

    output_path = _audio_output_path(project_id, scene)
    scene.status = JobStatus.RUNNING
    db.commit()

    try:
        provider = get_tts_provider()
        await provider.synthesize(scene.narration, output_path, project.language)
    except TTSProviderError as exc:
        scene.status = JobStatus.FAILED
        scene.attempt_count += 1
        scene.last_error = str(exc)
        db.add(
            GenerationLog(
                project_id=project_id,
                scene_id=scene.id,
                stage="audio_generation",
                level=LogLevel.ERROR,
                message=f"Audio generation failed for scene {scene.scene_number}",
                error_details=str(exc),
            )
        )
        db.commit()
        log.error(
            "scene_audio_generation_failed",
            project_id=str(project_id),
            scene_id=str(scene.id),
            error=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    scene.audio_path = output_path
    scene.status = JobStatus.SUCCEEDED
    scene.attempt_count += 1
    scene.last_error = None

    existing_asset = next(
        (
            a
            for a in scene.project.assets
            if a.scene_id == scene.id and a.asset_type == AssetType.AUDIO_NARRATION
        ),
        None,
    )
    if existing_asset:
        existing_asset.file_path = output_path
        existing_asset.provider = settings.tts_provider
        existing_asset.prompt_used = scene.narration
    else:
        db.add(
            Asset(
                project_id=project_id,
                scene_id=scene.id,
                asset_type=AssetType.AUDIO_NARRATION,
                file_path=output_path,
                provider=settings.tts_provider,
                prompt_used=scene.narration,
            )
        )

    db.add(
        GenerationLog(
            project_id=project_id,
            scene_id=scene.id,
            stage="audio_generation",
            level=LogLevel.INFO,
            message=f"Audio generated for scene {scene.scene_number} via {settings.tts_provider}",
        )
    )
    db.commit()
    db.refresh(scene)
    log.info(
        "scene_audio_generated",
        project_id=str(project_id),
        scene_id=str(scene.id),
        path=output_path,
    )
    return scene


async def generate_project_audio(project_id: uuid.UUID, db: Session) -> Project:
    """
    Generate narration audio for every scene in a project that doesn't
    already have it. Requires the project to have images already
    (VISUALS_GENERATED) or to already be mid-way through audio
    (VOICE_GENERATED, for a retry after a partial failure). Moves the
    project to VOICE_GENERATED once every scene succeeds; leaves it at
    VISUALS_GENERATED if any scene still failed after this pass.
    """
    project = _get_project_or_404(project_id, db)

    if project.status not in (ProjectStatus.VISUALS_GENERATED, ProjectStatus.VOICE_GENERATED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Project must have scene images before generating audio "
                f"(current status: {project.status.value})"
            ),
        )

    scenes: List[Scene] = sorted(project.scenes, key=lambda s: s.scene_number)
    if not scenes:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project has no scenes")

    pending_scenes = [s for s in scenes if s.audio_path is None]
    failures = []

    for scene in pending_scenes:
        try:
            await generate_scene_audio(project_id, scene.id, db)
        except HTTPException as exc:
            failures.append({"scene_number": scene.scene_number, "error": exc.detail})

    db.refresh(project)
    all_succeeded = all(s.audio_path is not None for s in project.scenes)

    if all_succeeded:
        project.status = ProjectStatus.VOICE_GENERATED
        db.add(
            GenerationLog(
                project_id=project_id,
                stage="audio_completed",
                level=LogLevel.INFO,
                message=f"All {len(scenes)} scene audio files generated",
            )
        )
        db.commit()
        db.refresh(project)
        log.info("project_audio_completed", project_id=str(project_id), scene_count=len(scenes))
        return project

    # Not every scene has audio yet — the project must not claim
    # VOICE_GENERATED (that status is an invariant: it only holds when
    # every scene has narration audio).
    project.status = ProjectStatus.VISUALS_GENERATED
    db.add(
        GenerationLog(
            project_id=project_id,
            stage="audio_partial_failure",
            level=LogLevel.ERROR,
            message=f"{len(failures)} of {len(scenes)} scenes failed audio generation",
            error_details=str(failures),
        )
    )
    db.commit()
    log.error("project_audio_partial_failure", project_id=str(project_id), failures=failures)
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "message": "Some scenes failed audio generation. Fix the underlying issue and call this endpoint again — already-successful scenes are skipped.",
            "failures": failures,
        },
    )
