"""
Asset: any generated file belonging to a project — image, narration audio,
music, captions, per-scene video, final video, or thumbnail (section 22
assets/ directory). Kept as its own table so assets can be listed, swapped,
or regenerated independently of the Project/Scene rows that reference them.
"""

import uuid
from typing import Optional

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import AssetType, TimestampMixin, UUIDPKMixin


class Asset(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "assets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=True, index=True
    )

    asset_type: Mapped[AssetType] = mapped_column(
        SAEnum(AssetType, name="asset_type", native_enum=False, length=32), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # e.g. "openai_images", "edge_tts"
    prompt_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="assets")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset type={self.asset_type} path={self.file_path}>"
