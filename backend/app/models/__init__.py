"""
Import every model here so that:
  1. `Base.metadata.create_all()` (used only in tests) sees all tables.
  2. Alembic's autogenerate can discover all tables via `from app.models import *`.
  3. Relationship string references (e.g. Mapped["Scene"]) resolve correctly.
"""

from app.database import Base  # noqa: F401
from app.models.asset import Asset  # noqa: F401
from app.models.generation_log import GenerationLog  # noqa: F401
from app.models.job import Job  # noqa: F401
from app.models.project import Project  # noqa: F401
from app.models.scene import Scene  # noqa: F401
from app.models.youtube_credential import YouTubeCredential  # noqa: F401
from app.models.youtube_upload import YouTubeUpload  # noqa: F401

__all__ = [
    "Base",
    "Asset",
    "GenerationLog",
    "Job",
    "Project",
    "Scene",
    "YouTubeCredential",
    "YouTubeUpload",
]
