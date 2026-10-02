"""
GenerationLog: structured, queryable log of pipeline events per project
(and optionally per scene) — a DB-backed complement to the structlog
console/JSON logs in app/utils/logger.py (section 26). This is what
powers a "what happened to this project" view in the UI, which plain
stdout logs can't easily provide.
"""

import uuid
from typing import Optional

from sqlalchemy import Enum as SAEnum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import LogLevel, TimestampMixin, UUIDPKMixin


class GenerationLog(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "generation_logs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=True, index=True
    )

    stage: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "script_generation", "render"
    level: Mapped[LogLevel] = mapped_column(
        SAEnum(LogLevel, name="log_level", native_enum=False, length=16),
        default=LogLevel.INFO,
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    error_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="generation_logs")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GenerationLog stage={self.stage} level={self.level}>"
