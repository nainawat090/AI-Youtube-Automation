"""
Phase 9: thumbnail generation.

Generates a standalone 1280x720 YouTube thumbnail: an eye-catching
background image from the configured VisualProvider (the same
AI-image/stock-photo provider Phase 5 uses for scenes, just with a
thumbnail-specific prompt), then the video's youtube_title burned on top
inside a translucent banner for readability, via the same ffmpeg
drawtext technique Phase 7 uses for on-screen text (font/escaping helpers
are imported from render_service, not re-derived, so Hindi titles get the
correct Devanagari font here too).

This is a side-channel action, not a pipeline stage: it only needs the
project's script to already exist (youtube_title + at least one scene's
visual_prompt for a topical background), so it doesn't touch
project.status and can be called any time after script generation,
independent of how far along images/audio/render/captions are. Safe to
call again — always regenerates both the background image and the
composited thumbnail from scratch.
"""

import os
import uuid
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.asset import Asset
from app.models.base import AssetType, LogLevel
from app.models.generation_log import GenerationLog
from app.models.project import Project
from app.services.render_service import RenderError, _escape_drawtext, _font_for_language, _run_ffmpeg
from app.services.visual_provider import VisualProviderError, get_visual_provider
from app.utils.logger import get_logger

log = get_logger(__name__)

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720
MAX_CHARS_PER_LINE = 22
FONT_SIZE = 64
LINE_HEIGHT = 76
BANNER_PADDING = 28
BANNER_MARGIN_BOTTOM = 50


def _get_project_or_404(project_id: uuid.UUID, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _raw_background_path(project_id: uuid.UUID) -> str:
    return os.path.join(settings.local_storage_path, "thumbnails", str(project_id), "background_raw.png")


def _thumbnail_output_path(project_id: uuid.UUID) -> str:
    return os.path.join(settings.local_storage_path, "thumbnails", str(project_id), "thumbnail.jpg")


def _wrap_title(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> List[str]:
    """Greedy word-wrap so the title fits the 1280-wide banner at FONT_SIZE."""
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
    return lines


def _build_thumbnail_prompt(project: Project) -> str:
    topic_hint = project.scenes[0].visual_prompt if project.scenes and project.scenes[0].visual_prompt else project.title
    return (
        f"A bold, vibrant, high-contrast YouTube thumbnail background image for a video "
        f"about: {topic_hint}. Cinematic lighting, dramatic composition, eye-catching, "
        f"professional thumbnail photography style. No text, no words, no watermark, no logos."
    )


async def _composite_thumbnail(raw_image_path: str, title: str, language: str, output_path: str) -> None:
    lines = _wrap_title(title)
    font = _font_for_language(language)

    banner_h = len(lines) * LINE_HEIGHT + 2 * BANNER_PADDING
    banner_y = THUMBNAIL_HEIGHT - banner_h - BANNER_MARGIN_BOTTOM

    vf_parts = [
        f"scale={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}",
        f"drawbox=x=0:y={banner_y}:w={THUMBNAIL_WIDTH}:h={banner_h}:color=black@0.55:t=fill",
    ]
    for i, line in enumerate(lines):
        y = banner_y + BANNER_PADDING + i * LINE_HEIGHT
        escaped = _escape_drawtext(line)
        vf_parts.append(
            f"drawtext=fontfile='{font}':text='{escaped}':fontsize={FONT_SIZE}:fontcolor=white:"
            f"borderw=4:bordercolor=black@0.9:x=(w-text_w)/2:y={y}"
        )

    vf = ",".join(vf_parts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    await _run_ffmpeg(["-i", raw_image_path, "-vf", vf, "-q:v", "2", output_path])


async def generate_project_thumbnail(project_id: uuid.UUID, db: Session) -> Project:
    """
    Generate a fresh AI background image and composite the video's
    youtube_title onto it as a 1280x720 thumbnail. Requires the project's
    script to already exist (POST .../generate-script) so a youtube_title
    and at least one scene's visual_prompt are available.
    """
    project = _get_project_or_404(project_id, db)

    if not project.youtube_title:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project has no youtube_title yet. Call POST .../generate-script first.",
        )

    prompt = _build_thumbnail_prompt(project)
    raw_path = _raw_background_path(project_id)

    try:
        provider = get_visual_provider()
        await provider.generate_image(prompt, raw_path)
    except VisualProviderError as exc:
        db.add(
            GenerationLog(
                project_id=project_id,
                stage="thumbnail",
                level=LogLevel.ERROR,
                message="Thumbnail background image generation failed",
                error_details=str(exc),
            )
        )
        db.commit()
        log.error("thumbnail_background_failed", project_id=str(project_id), error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    output_path = _thumbnail_output_path(project_id)
    try:
        await _composite_thumbnail(raw_path, project.youtube_title, project.language, output_path)
    except RenderError as exc:
        db.add(
            GenerationLog(
                project_id=project_id, stage="thumbnail", level=LogLevel.ERROR, message=str(exc)
            )
        )
        db.commit()
        log.error("thumbnail_composite_failed", project_id=str(project_id), error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    project.thumbnail_path = output_path

    existing_asset = next((a for a in project.assets if a.asset_type == AssetType.THUMBNAIL), None)
    if existing_asset:
        existing_asset.file_path = output_path
        existing_asset.provider = settings.visual_provider
        existing_asset.prompt_used = prompt
    else:
        db.add(
            Asset(
                project_id=project_id,
                asset_type=AssetType.THUMBNAIL,
                file_path=output_path,
                provider=settings.visual_provider,
                prompt_used=prompt,
            )
        )

    db.add(
        GenerationLog(
            project_id=project_id,
            stage="thumbnail",
            level=LogLevel.INFO,
            message="Thumbnail generated",
        )
    )
    db.commit()
    db.refresh(project)
    log.info("thumbnail_generated", project_id=str(project_id), path=output_path)
    return project
