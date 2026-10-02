"""
Scene: one row per scene within a project (section 7). Designed so a single
scene can be regenerated / edited without touching the rest of the project.
"""

import uuid
from typing import Optional

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import JobStatus, TimestampMixin, UUIDPKMixin


class Scene(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "scenes"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    scene_number: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    narration: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visual_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    on_screen_text: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    transition: Mapped[Optional[str]] = mapped_column(String(64), default="fade", nullable=True)

    image_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    audio_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    scene_video_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="scene_status", native_enum=False, length=32),
        default=JobStatus.PENDING,
        nullable=False,
    )

    # Retry tracking (section 21)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="scenes")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Scene project_id={self.project_id} number={self.scene_number}>"
