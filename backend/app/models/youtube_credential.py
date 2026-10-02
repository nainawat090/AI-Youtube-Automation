"""
YouTubeCredential: stores the OAuth 2.0 tokens for the ONE YouTube channel
this self-hosted deployment uploads to (Phase 12).

There's no multi-user auth system anywhere in this project (see
PROJECT.md) — one deployment, one connected channel — so this is
intentionally a singleton table: connecting a new channel replaces the
existing row rather than adding a second one (see
app/services/youtube_oauth_service.py).

Tokens are stored in plain text, matching the scope of a single-operator
self-hosted tool. If this were ever exposed to multiple untrusted
operators, encrypting access_token/refresh_token at rest (e.g. via a KMS
or Fernet key from a secret manager) would be the next thing to add here.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class YouTubeCredential(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "youtube_credentials"

    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)

    channel_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    channel_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<YouTubeCredential channel={self.channel_title!r}>"
