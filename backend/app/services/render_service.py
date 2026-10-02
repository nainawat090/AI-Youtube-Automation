"""
Phase 7: FFmpeg renderer.

Turns each scene's (image, narration audio) pair into a short video clip —
the image held still for exactly the audio's duration, with the scene's
on_screen_text burned in near the bottom — then stitches every scene clip
into one final project video.

Transitions: a scene's `transition` field is either "cut" (a hard cut) or
anything else, treated as a 0.4s crossfade (video AND audio together, so
they never drift out of sync). A brief video+audio crossfade at scene
boundaries is the standard technique for narrated video like this; a longer
crossfade would blur two different narrations together, so it's kept short
on purpose. Finer-grained transition types (wipe, slide, etc.) aren't
implemented — every non-"cut" value currently produced by the LLM is
"fade", which is exactly this crossfade.

No official ffmpeg Python binding is used — the ffmpeg/ffprobe CLIs
(already installed in the Docker image for exactly this purpose) are
invoked directly via asyncio subprocesses, same "raw, no unnecessary SDK"
convention as the AI/visual/TTS providers.
"""

import asyncio
import os
import uuid
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.asset import Asset
from app.models.base import AssetType, JobStatus, LogLevel, ProjectStatus
from app.models.generation_log import GenerationLog
from app.models.project import Project
from app.models.scene import Scene
from app.utils.logger import get_logger

log = get_logger(__name__)

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
CROSSFADE_SECONDS = 0.4

# Font paths installed by the Dockerfile (fonts-dejavu-core, fonts-noto-core).
# Devanagari needs its own font — DejaVu has no Devanagari glyphs — so a
# Hindi-language project's on-screen text gets a font that can actually draw
# it, rather than silently rendering as tofu boxes.
FONT_LATIN = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_DEVANAGARI = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"


class RenderError(Exception):
    """Raised when an ffmpeg/ffprobe step fails."""


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


def _escape_drawtext(text: str) -> str:
    """Escape a string for safe use inside an ffmpeg drawtext filter value."""
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "’")  # smart-quote a straight apostrophe rather than escape it
        .replace("%", "\\%")
    )


def _font_for_language(language: str) -> str:
    return FONT_DEVANAGARI if language.lower().startswith("hi") else FONT_LATIN


async def _run_ffmpeg(args: List[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RenderError(f"ffmpeg failed (exit {process.returncode}): {stderr.decode(errors='replace')[:2000]}")


async def _probe_duration(file_path: str) -> float:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        file_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RenderError(f"ffprobe failed on {file_path}: {stderr.decode(errors='replace')[:500]}")
    try:
        return float(stdout.decode().strip())
    except ValueError as exc:
        raise RenderError(f"ffprobe returned no duration for {file_path}") from exc


def _scene_clip_path(project_id: uuid.UUID, scene: Scene) -> str:
    directory = os.path.join(settings.local_storage_path, "videos", str(project_id))
    return os.path.join(directory, f"scene_{scene.scene_number:02d}.mp4")


def _final_video_path(project_id: uuid.UUID) -> str:
    directory = os.path.join(settings.output_path, str(project_id))
    return os.path.join(directory, "final.mp4")


async def render_scene_clip(project_id: uuid.UUID, scene_id: uuid.UUID, db: Session) -> Scene:
    """
    Render exactly one scene's video clip: its image held for its audio's
    exact duration, with on_screen_text burned in if present. Requires the
    scene to already have both an image and audio.
    """
    project = _get_project_or_404(project_id, db)
    scene = _get_scene_or_404(project_id, scene_id, db)

    if not scene.image_path or not os.path.exists(scene.image_path):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Scene {scene.scene_number} has no image yet — run image generation first",
        )
    if not scene.audio_path or not os.path.exists(scene.audio_path):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Scene {scene.scene_number} has no audio yet — run audio generation first",
        )

    output_path = _scene_clip_path(project_id, scene)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    scene.status = JobStatus.RUNNING
    db.commit()

    try:
        duration = await _probe_duration(scene.audio_path)

        vf_parts = [
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase",
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
        ]
        if scene.on_screen_text:
            font = _font_for_language(project.language)
            escaped = _escape_drawtext(scene.on_screen_text)
            vf_parts.append(
                f"drawtext=fontfile={font}:text='{escaped}':fontsize=54:fontcolor=white:"
                f"borderw=3:bordercolor=black@0.8:x=(w-text_w)/2:y=h-th-80"
            )
        video_filter = ",".join(vf_parts)

        await _run_ffmpeg(
            [
                "-loop", "1",
                "-i", scene.image_path,
                "-i", scene.audio_path,
                "-vf", video_filter,
                "-c:v", "libx264",
                "-tune", "stillimage",
                "-c:a", "aac",
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-t", f"{duration:.3f}",
                output_path,
            ]
        )
    except RenderError as exc:
        scene.status = JobStatus.FAILED
        scene.attempt_count += 1
        scene.last_error = str(exc)
        db.add(
            GenerationLog(
                project_id=project_id,
                scene_id=scene.id,
                stage="scene_render",
                level=LogLevel.ERROR,
                message=f"Render failed for scene {scene.scene_number}",
                error_details=str(exc),
            )
        )
        db.commit()
        log.error("scene_render_failed", project_id=str(project_id), scene_id=str(scene.id), error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    scene.scene_video_path = output_path
    scene.status = JobStatus.SUCCEEDED
    scene.attempt_count += 1
    scene.last_error = None

    existing_asset = next(
        (a for a in scene.project.assets if a.scene_id == scene.id and a.asset_type == AssetType.SCENE_VIDEO),
        None,
    )
    if existing_asset:
        existing_asset.file_path = output_path
    else:
        db.add(
            Asset(
                project_id=project_id,
                scene_id=scene.id,
                asset_type=AssetType.SCENE_VIDEO,
                file_path=output_path,
                provider="ffmpeg",
            )
        )

    db.add(
        GenerationLog(
            project_id=project_id,
            scene_id=scene.id,
            stage="scene_render",
            level=LogLevel.INFO,
            message=f"Scene {scene.scene_number} rendered",
        )
    )
    db.commit()
    db.refresh(scene)
    log.info("scene_rendered", project_id=str(project_id), scene_id=str(scene.id), path=output_path)
    return scene


async def _concat_cut(clip_paths: List[str], output_path: str) -> None:
    """Fast path: hard cuts only, stream-copy concat (no re-encode)."""
    list_file = output_path + ".txt"
    with open(list_file, "w") as f:
        for path in clip_paths:
            f.write(f"file '{os.path.abspath(path)}'\n")
    try:
        await _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])
    finally:
        os.remove(list_file)


async def _concat_crossfade(clip_paths: List[str], output_path: str) -> None:
    """
    Chain xfade (video) + acrossfade (audio) across every clip so a scene
    boundary is a short crossfade instead of a hard cut, keeping video and
    audio perfectly in sync throughout (see module docstring).
    """
    durations = [await _probe_duration(p) for p in clip_paths]

    inputs: List[str] = []
    for p in clip_paths:
        inputs += ["-i", p]

    filter_parts = []
    running_video = "0:v"
    running_audio = "0:a"
    running_duration = durations[0]

    for i in range(1, len(clip_paths)):
        offset = max(running_duration - CROSSFADE_SECONDS, 0)
        v_out = f"v{i}"
        a_out = f"a{i}"
        filter_parts.append(
            f"[{running_video}][{i}:v]xfade=transition=fade:duration={CROSSFADE_SECONDS}:offset={offset:.3f}[{v_out}]"
        )
        filter_parts.append(f"[{running_audio}][{i}:a]acrossfade=d={CROSSFADE_SECONDS}[{a_out}]")
        running_video = v_out
        running_audio = a_out
        running_duration = running_duration + durations[i] - CROSSFADE_SECONDS

    filter_complex = ";".join(filter_parts)

    await _run_ffmpeg(
        [
            *inputs,
            "-filter_complex", filter_complex,
            "-map", f"[{running_video}]",
            "-map", f"[{running_audio}]",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
    )


async def render_project_video(project_id: uuid.UUID, db: Session) -> Project:
    """
    Render every scene that doesn't already have a clip, then stitch all
    scene clips into the final project video. Requires the project to have
    narration audio for every scene (VOICE_GENERATED), to already be
    mid-render (RENDERING, for a retry after a partial failure), or to
    already be READY_FOR_REVIEW (so this endpoint stays callable again —
    e.g. to re-cut the final video after editing one scene, which clears
    that scene's scene_video_path and lets it be re-rendered and re-stitched
    in — matching the same before/after-state pattern used by the
    generate-images and generate-audio endpoints). Moves the project to
    READY_FOR_REVIEW once the final video exists.
    """
    project = _get_project_or_404(project_id, db)

    if project.status not in (
        ProjectStatus.VOICE_GENERATED,
        ProjectStatus.RENDERING,
        ProjectStatus.READY_FOR_REVIEW,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Project must have narration audio for every scene before rendering "
                f"(current status: {project.status.value})"
            ),
        )

    scenes: List[Scene] = sorted(project.scenes, key=lambda s: s.scene_number)
    if not scenes:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project has no scenes")

    project.status = ProjectStatus.RENDERING
    db.commit()

    failures = []
    for scene in scenes:
        if scene.scene_video_path and os.path.exists(scene.scene_video_path):
            continue
        try:
            await render_scene_clip(project_id, scene.id, db)
        except HTTPException as exc:
            failures.append({"scene_number": scene.scene_number, "error": exc.detail})

    if failures:
        project.status = ProjectStatus.VOICE_GENERATED
        db.add(
            GenerationLog(
                project_id=project_id,
                stage="render_partial_failure",
                level=LogLevel.ERROR,
                message=f"{len(failures)} of {len(scenes)} scenes failed to render",
                error_details=str(failures),
            )
        )
        db.commit()
        log.error("project_render_partial_failure", project_id=str(project_id), failures=failures)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "Some scenes failed to render. Fix the issue and call this endpoint again — already-rendered scenes are skipped.",
                "failures": failures,
            },
        )

    db.refresh(project)
    scenes = sorted(project.scenes, key=lambda s: s.scene_number)
    clip_paths = [s.scene_video_path for s in scenes]
    output_path = _final_video_path(project_id)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    all_cuts = all((s.transition or "cut") == "cut" for s in scenes[1:])

    try:
        if all_cuts:
            await _concat_cut(clip_paths, output_path)
        else:
            await _concat_crossfade(clip_paths, output_path)
    except RenderError as exc:
        project.status = ProjectStatus.VOICE_GENERATED
        db.add(
            GenerationLog(
                project_id=project_id,
                stage="final_render",
                level=LogLevel.ERROR,
                message="Final video assembly failed",
                error_details=str(exc),
            )
        )
        db.commit()
        log.error("project_final_render_failed", project_id=str(project_id), error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    project.video_path = output_path
    project.status = ProjectStatus.READY_FOR_REVIEW

    existing_asset = next(
        (a for a in project.assets if a.scene_id is None and a.asset_type == AssetType.FINAL_VIDEO),
        None,
    )
    if existing_asset:
        existing_asset.file_path = output_path
    else:
        db.add(
            Asset(
                project_id=project_id,
                scene_id=None,
                asset_type=AssetType.FINAL_VIDEO,
                file_path=output_path,
                provider="ffmpeg",
            )
        )

    db.add(
        GenerationLog(
            project_id=project_id,
            stage="final_render",
            level=LogLevel.INFO,
            message=f"Final video assembled from {len(scenes)} scenes",
        )
    )
    db.commit()
    db.refresh(project)
    log.info("project_rendered", project_id=str(project_id), path=output_path)
    return project
