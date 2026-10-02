"""
Phase 8: captions.

Generates an SRT caption file from every scene's narration text, timed to
line up with the ALREADY-RENDERED final video's actual timeline (Phase 7),
then burns those captions onto that video with ffmpeg's `subtitles` filter
(libass) so the delivered video is self-contained — no separate .srt file
required for a viewer to see captions.

Design note on where this sits in the pipeline: Phase 7 deliberately skips
ProjectStatus.CAPTIONS_GENERATED and goes straight from VOICE_GENERATED to
READY_FOR_REVIEW (see render_service.py's module docstring). This module
builds on top of that rather than inserting itself before rendering: its
precondition is READY_FOR_REVIEW (a final video must already exist), and it
leaves the project at READY_FOR_REVIEW afterwards too — captions refine an
already-reviewable video rather than gating a new pipeline stage. Calling
this endpoint again always regenerates the SRT and re-burns from scratch
onto the CURRENT final video, so "edit a scene's narration -> re-render ->
re-caption" is always safe to repeat.

Caption timing deliberately reuses render_service's own duration-probing
and crossfade-offset math (imported, not re-derived) so a caption's start/
end time always matches where that scene actually falls in the stitched
video, whether the project used hard cuts or a crossfade throughout.
"""

import os
import uuid
from typing import List, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.asset import Asset
from app.models.base import AssetType, LogLevel, ProjectStatus
from app.models.generation_log import GenerationLog
from app.models.project import Project
from app.models.scene import Scene
from app.services.render_service import (
    CROSSFADE_SECONDS,
    RenderError,
    _final_video_path,
    _probe_duration,
    _run_ffmpeg,
)
from app.utils.logger import get_logger

log = get_logger(__name__)

MAX_CHARS_PER_LINE = 42


def _get_project_or_404(project_id: uuid.UUID, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _captions_path(project_id: uuid.UUID) -> str:
    return f"{settings.output_path}/{project_id}/captions.srt"


def _captioned_video_path(project_id: uuid.UUID) -> str:
    return f"{settings.output_path}/{project_id}/final_captioned.mp4"


def _ass_font_family(language: str) -> str:
    return "Noto Sans Devanagari" if language.lower().startswith("hi") else "DejaVu Sans"


def _wrap_text(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> str:
    """Greedy word-wrap so a caption line stays readable at 1920x1080."""
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def _srt_timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        millis = 0
        secs += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _compute_scene_timeline(durations: List[float], all_cuts: bool) -> List[Tuple[float, float]]:
    """
    Mirrors render_service.render_project_video's own concat math exactly,
    so caption timestamps line up with scene boundaries in the actual
    stitched video. Phase 7 always uses ONE strategy for the whole video
    (never a per-junction mix of cuts and crossfades — see the `all_cuts`
    check there), so the same is true here.
    """
    if not durations:
        return []

    if all_cuts:
        timeline: List[Tuple[float, float]] = []
        t = 0.0
        for d in durations:
            timeline.append((t, t + d))
            t += d
        return timeline

    timeline = [(0.0, durations[0])]
    running_duration = durations[0]
    for d in durations[1:]:
        start = max(running_duration - CROSSFADE_SECONDS, 0.0)
        running_duration = running_duration + d - CROSSFADE_SECONDS
        timeline.append((start, running_duration))
    return timeline


def _write_srt(scenes: List[Scene], timeline: List[Tuple[float, float]], srt_path: str) -> None:
    os.makedirs(os.path.dirname(srt_path), exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, (scene, (start, end)) in enumerate(zip(scenes, timeline), start=1):
            f.write(f"{idx}\n")
            f.write(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n")
            f.write(_wrap_text(scene.narration) + "\n\n")


async def _burn_captions(video_path: str, srt_path: str, font_family: str, output_path: str) -> None:
    # The subtitles filter treats ':' as an option separator and '\' / "'" as
    # escapes, so the filename (passed single-quoted) needs its own escapes.
    escaped_srt = srt_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    style = (
        f"FontName={font_family},Fontsize=26,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
        "Bold=1,Alignment=2,MarginV=60"
    )
    vf = f"subtitles='{escaped_srt}':force_style='{style}'"
    await _run_ffmpeg(
        ["-i", video_path, "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", output_path]
    )


async def generate_project_captions(project_id: uuid.UUID, db: Session) -> Project:
    """
    Build captions.srt from every scene's narration, timed to the already-
    rendered final video, and burn them in. Requires POST .../render to have
    already produced a final video. Safe to call again any time afterwards.
    """
    project = _get_project_or_404(project_id, db)

    if project.status != ProjectStatus.READY_FOR_REVIEW or not project.video_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Project must have a rendered final video before captions can be generated "
                f"(current status: {project.status.value}). Call POST .../render first."
            ),
        )

    # Always burn from the RAW stitched video that render_project_video
    # produced (not project.video_path, which a previous captions call may
    # have already repointed at final_captioned.mp4) — otherwise a second
    # call here would have ffmpeg read and write the same file at once.
    source_video_path = _final_video_path(project_id)
    if not os.path.exists(source_video_path):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Final video file is missing on disk")

    scenes: List[Scene] = sorted(project.scenes, key=lambda s: s.scene_number)
    if not scenes:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project has no scenes")

    missing_clips = [
        s.scene_number for s in scenes if not s.scene_video_path or not os.path.exists(s.scene_video_path)
    ]
    if missing_clips:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenes missing rendered clips, cannot compute caption timing: {missing_clips}",
        )

    try:
        durations = [await _probe_duration(s.scene_video_path) for s in scenes]
        all_cuts = all((s.transition or "cut") == "cut" for s in scenes[1:])
        timeline = _compute_scene_timeline(durations, all_cuts)

        srt_path = _captions_path(project_id)
        _write_srt(scenes, timeline, srt_path)

        font_family = _ass_font_family(project.language)
        captioned_path = _captioned_video_path(project_id)
        await _burn_captions(source_video_path, srt_path, font_family, captioned_path)
    except RenderError as exc:
        db.add(
            GenerationLog(
                project_id=project_id, stage="captions", level=LogLevel.ERROR, message=str(exc)
            )
        )
        db.commit()
        log.error("captions_failed", project_id=str(project_id), error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    project.captions_path = srt_path
    project.video_path = captioned_path

    srt_asset = next((a for a in project.assets if a.asset_type == AssetType.CAPTION_SRT), None)
    if srt_asset is None:
        db.add(Asset(project_id=project_id, asset_type=AssetType.CAPTION_SRT, file_path=srt_path))
    else:
        srt_asset.file_path = srt_path

    final_asset = next(
        (a for a in project.assets if a.asset_type == AssetType.FINAL_VIDEO and a.scene_id is None), None
    )
    if final_asset is None:
        db.add(Asset(project_id=project_id, asset_type=AssetType.FINAL_VIDEO, file_path=captioned_path))
    else:
        final_asset.file_path = captioned_path

    db.add(
        GenerationLog(
            project_id=project_id,
            stage="captions",
            level=LogLevel.INFO,
            message=f"Captions generated and burned in for {len(scenes)} scenes",
        )
    )
    db.commit()
    db.refresh(project)
    log.info("captions_generated", project_id=str(project_id))
    return project
