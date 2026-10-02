"""
Script generation service (section 4/5, phase 3). Takes a Project's user
prompt, asks the configured AIProvider for a structured JSON video plan,
validates it against ScriptPlan, repairs it via a follow-up LLM call if
invalid, and persists the result onto the Project + creates Scene rows.
"""

import json
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import LogLevel, ProjectStatus
from app.models.generation_log import GenerationLog
from app.models.project import Project
from app.models.scene import Scene
from app.schemas.script import ScriptPlan
from app.services.ai_provider import AIProvider, AIProviderError, get_ai_provider
from app.utils.logger import get_logger

log = get_logger(__name__)


SYSTEM_PROMPT = """You are a video scriptwriter and instructional designer for short \
educational YouTube videos. You ALWAYS respond with a single JSON object and nothing \
else — no markdown fences, no commentary before or after the JSON.

The JSON object must match this shape exactly:
{
  "project_title": string,
  "target_audience": string,
  "duration_seconds": integer,
  "language": string (BCP-47 locale code, e.g. "en-US", "hi-IN"),
  "style": string (e.g. "Educational", "Cinematic", "Energetic"),
  "youtube_title": string (under 100 characters, engaging but not misleading),
  "youtube_description": string,
  "youtube_tags": array of strings (5-15 relevant tags),
  "scenes": [
    {
      "scene_number": integer (starting at 1, sequential),
      "duration_seconds": integer (3-120),
      "narration": string (what the narrator says in this scene),
      "visual_prompt": string (a detailed prompt for an AI image generator describing \
what should be shown on screen for this scene),
      "on_screen_text": string or null (short text overlay, if any),
      "transition": string (e.g. "fade", "cut", "slide")
    }
  ]
}

Rules:
- The sum of all scenes' duration_seconds should be close to the requested total duration_seconds.
- Write narration appropriate for the stated target audience's age and knowledge level.
- Never include copyrighted characters, brand names, or real public figures in visual_prompt.
- Keep youtube_title under 100 characters and not misleading (section 14 of the brief)."""


def _build_user_prompt(project: Project) -> str:
    parts = [f"User request: {project.user_prompt}"]
    if project.target_audience:
        parts.append(f"Target audience: {project.target_audience}")
    if project.duration_seconds:
        parts.append(f"Target total duration: {project.duration_seconds} seconds")
    parts.append(f"Language: {project.language}")
    return "\n".join(parts)


def _log(db: Session, project_id, stage: str, level: LogLevel, message: str, error_details: Optional[str] = None) -> None:
    db.add(GenerationLog(project_id=project_id, stage=stage, level=level, message=message, error_details=error_details))


def _parse_and_validate(raw_text: str) -> ScriptPlan:
    """Parse raw LLM text as JSON and validate against ScriptPlan. Raises on failure."""
    cleaned = raw_text.strip()
    # Some models wrap JSON in ```json fences despite instructions — strip if present.
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    data = json.loads(cleaned)  # raises json.JSONDecodeError on invalid JSON
    return ScriptPlan.model_validate(data)  # raises ValidationError on schema mismatch


async def _generate_with_repair(provider: AIProvider, user_prompt: str) -> ScriptPlan:
    """Call the LLM, validate the JSON, and retry with a repair prompt on failure."""
    last_error: Optional[str] = None
    current_user_prompt = user_prompt

    for attempt in range(1, settings.max_retries + 1):
        raw = await provider.generate(SYSTEM_PROMPT, current_user_prompt)
        try:
            return _parse_and_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            log.warning("script_json_invalid", attempt=attempt, error=last_error[:500])
            current_user_prompt = (
                f"{user_prompt}\n\n"
                f"Your previous response was invalid: {last_error}\n"
                f"Previous response was:\n{raw}\n\n"
                f"Return ONLY a corrected, valid JSON object matching the required shape."
            )

    raise AIProviderError(f"LLM failed to produce valid script JSON after {settings.max_retries} attempts: {last_error}")


async def generate_script(project_id, db: Session) -> Project:
    """
    Full Phase 3 flow: load project, call the LLM, validate, persist onto
    Project + create Scene rows. Raises on unrecoverable failure; caller is
    responsible for catching and setting Project.status = FAILED if desired.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    project.status = ProjectStatus.PLANNING
    _log(db, project.id, "script_generation", LogLevel.INFO, "Script generation started")
    db.commit()

    try:
        provider = get_ai_provider()
    except AIProviderError as exc:
        project.status = ProjectStatus.FAILED
        project.last_error = str(exc)
        _log(db, project.id, "script_generation", LogLevel.ERROR, "AI provider unavailable", error_details=str(exc))
        db.commit()
        raise

    user_prompt = _build_user_prompt(project)

    try:
        plan = await _generate_with_repair(provider, user_prompt)
    except Exception as exc:  # noqa: BLE001 - any failure here means the stage failed
        project.status = ProjectStatus.FAILED
        project.last_error = str(exc)[:2000]
        _log(db, project.id, "script_generation", LogLevel.ERROR, "Script generation failed", error_details=str(exc)[:4000])
        db.commit()
        raise

    # Persist the plan onto the project
    project.title = plan.project_title
    project.target_audience = plan.target_audience
    project.duration_seconds = plan.duration_seconds
    project.language = plan.language
    project.youtube_title = plan.youtube_title
    project.youtube_description = plan.youtube_description
    project.youtube_tags = ", ".join(plan.youtube_tags)
    project.status = ProjectStatus.SCRIPT_GENERATED

    # Replace any existing scenes (e.g. on regeneration) and create fresh ones
    project.scenes.clear()
    for scene_plan in plan.scenes:
        project.scenes.append(
            Scene(
                scene_number=scene_plan.scene_number,
                duration_seconds=scene_plan.duration_seconds,
                narration=scene_plan.narration,
                visual_prompt=scene_plan.visual_prompt,
                on_screen_text=scene_plan.on_screen_text,
                transition=scene_plan.transition,
            )
        )

    _log(
        db, project.id, "script_generation", LogLevel.INFO,
        f"Script generated: {len(plan.scenes)} scenes, title='{plan.project_title}'",
    )
    db.commit()
    db.refresh(project)

    log.info("script_generation_completed", project_id=str(project.id), scene_count=len(plan.scenes))
    return project
