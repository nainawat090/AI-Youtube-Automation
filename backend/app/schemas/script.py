"""
The structured JSON contract the LLM must return for a video plan (section 4).
Kept strict so a malformed or incomplete LLM response fails validation loudly
rather than silently propagating bad data into scenes/rendering later.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ScriptScenePlan(BaseModel):
    scene_number: int = Field(..., ge=1)
    duration_seconds: int = Field(..., ge=3, le=120)
    narration: str = Field(..., min_length=1, max_length=2000)
    visual_prompt: str = Field(..., min_length=1, max_length=1000)
    on_screen_text: Optional[str] = Field(default=None, max_length=120)
    transition: str = Field(default="fade", max_length=32)


class ScriptPlan(BaseModel):
    """The full structured output the LLM must produce for one video."""

    project_title: str = Field(..., min_length=1, max_length=255)
    target_audience: str = Field(..., min_length=1, max_length=255)
    duration_seconds: int = Field(..., ge=30, le=1800)
    language: str = Field(..., min_length=2, max_length=16)
    style: str = Field(..., min_length=1, max_length=64)

    youtube_title: str = Field(..., min_length=1, max_length=100)
    youtube_description: str = Field(..., min_length=1, max_length=5000)
    youtube_tags: List[str] = Field(..., min_length=1, max_length=30)

    scenes: List[ScriptScenePlan] = Field(..., min_length=1, max_length=40)

    @field_validator("scenes")
    @classmethod
    def scene_numbers_are_sequential(cls, scenes: List[ScriptScenePlan]) -> List[ScriptScenePlan]:
        expected = list(range(1, len(scenes) + 1))
        actual = [s.scene_number for s in scenes]
        if actual != expected:
            raise ValueError(
                f"scene_number values must be sequential starting at 1, got {actual}"
            )
        return scenes
