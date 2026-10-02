"""Pydantic schemas for the Phase 14 n8n inbound-trigger endpoint."""

from typing import Optional

from pydantic import BaseModel, Field


class N8nTriggerRequest(BaseModel):
    """Body for POST /api/webhooks/n8n/trigger — mirrors ProjectCreate,
    since this endpoint's whole job is to create a project and start it."""

    user_prompt: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="The video request, e.g. 'Create a 5-minute video explaining how robots use sensors.'",
    )
    language: str = Field(default="en-US", description="BCP-47 locale, e.g. 'en-US', 'hi-IN'.")
    target_audience: Optional[str] = Field(default=None, max_length=255)
    duration_seconds: Optional[int] = Field(default=None, ge=30, le=1800)
