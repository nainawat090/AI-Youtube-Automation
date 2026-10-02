"""
YouTubeUpload: one row per upload attempt to YouTube (section 15/16/21).
Kept separate from Project so retries and history are preserved even
though Project also caches the current youtube_video_id/youtube_url for
quick access.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import JobStatus, TimestampMixin, UUIDPKMixin


class YouTubeUpload(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "youtube_uploads"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    youtube_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    privacy_status: Mapped[str] = mapped_column(String(16), default="private", nullable=False)

    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="youtube_upload_status", native_enum=False, length=32),
        default=JobStatus.PENDING,
        nullable=False,
    )

    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="youtube_uploads")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<YouTubeUpload project_id={self.project_id} status={self.status}>"
