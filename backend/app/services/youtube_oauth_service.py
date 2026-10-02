"""
Phase 12: YouTube OAuth 2.0.

Implements the standard OAuth "authorization code" flow directly against
Google's endpoints via httpx — no google-api-python-client / google-auth
SDK, same "raw HTTP, no unnecessary SDK" convention used by every provider
in this project:

  1. GET /api/youtube/oauth/login    -> build the Google consent-screen URL
  2. the user approves in their browser; Google redirects back with ?code=
  3. GET /api/youtube/oauth/callback -> exchange that code for an access
     token + refresh token, look up the connected channel, store both

There's no multi-user auth system in this project — one self-hosted
deployment connects to ONE YouTube channel — so YouTubeCredential is a
singleton table (see its own docstring): connecting again replaces the
existing row.

get_valid_access_token() is what Phase 13 (the actual video upload) will
call before every YouTube Data API request — it transparently refreshes an
expired access token using the stored refresh token, since access tokens
are only valid for about an hour and refresh tokens are the whole point of
requesting access_type=offline below.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.youtube_credential import YouTubeCredential
from app.utils.logger import get_logger

log = get_logger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"

# Refresh a little before actual expiry so a token already in flight for a
# request never goes stale mid-request.
_EXPIRY_SAFETY_MARGIN = timedelta(minutes=2)


class YouTubeOAuthError(Exception):
    """Raised when any step of the OAuth flow or a token refresh fails."""


def build_authorization_url(state: str) -> str:
    if not settings.youtube_client_id:
        raise YouTubeOAuthError("YOUTUBE_CLIENT_ID is not set in .env")
    params = {
        "client_id": settings.youtube_client_id,
        "redirect_uri": settings.youtube_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",  # required to receive a refresh_token
        "prompt": "consent",  # forces a fresh refresh_token even on a repeat connect
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def _fetch_channel_info(access_token: str) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort lookup of the connected channel's id/title, for display only."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                CHANNELS_URL,
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            if not items:
                return None, None
            channel = items[0]
            return channel["id"], channel["snippet"]["title"]
    except httpx.HTTPError as exc:
        log.warning("youtube_channel_lookup_failed", error=str(exc))
        return None, None


async def exchange_code_for_tokens(code: str, db: Session) -> YouTubeCredential:
    """
    Step 2 of the flow: trade the authorization code Google redirected back
    with for an access token + refresh token, then persist them (replacing
    any previously connected channel — see YouTubeCredential's docstring).
    """
    if not settings.youtube_client_id or not settings.youtube_client_secret:
        raise YouTubeOAuthError("YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET are not set in .env")

    body = {
        "code": code,
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "redirect_uri": settings.youtube_redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(TOKEN_URL, data=body)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.error("youtube_token_exchange_failed", status=exc.response.status_code, body=exc.response.text)
        raise YouTubeOAuthError(f"Google rejected the authorization code: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise YouTubeOAuthError(f"Network error exchanging code for tokens: {exc}") from exc

    payload = response.json()
    access_token = payload["access_token"]
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        # build_authorization_url always sends prompt=consent, which makes
        # Google issue a refresh_token even on a repeat connection, so this
        # should not normally happen — but fail loudly rather than quietly
        # storing a credential that can't be refreshed once the access
        # token expires in about an hour.
        raise YouTubeOAuthError(
            "Google did not return a refresh_token. Revoke this app's access at "
            "https://myaccount.google.com/permissions and try connecting again."
        )
    expires_in = payload.get("expires_in", 3600)
    scope = payload.get("scope", SCOPES)

    channel_id, channel_title = await _fetch_channel_info(access_token)

    credential = db.scalar(select(YouTubeCredential).limit(1))
    if credential is None:
        credential = YouTubeCredential()
        db.add(credential)

    credential.access_token = access_token
    credential.refresh_token = refresh_token
    credential.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    credential.scope = scope
    credential.channel_id = channel_id
    credential.channel_title = channel_title

    db.commit()
    db.refresh(credential)
    log.info("youtube_connected", channel_id=channel_id, channel_title=channel_title)
    return credential


async def _refresh_access_token(credential: YouTubeCredential, db: Session) -> None:
    body = {
        "refresh_token": credential.refresh_token,
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "grant_type": "refresh_token",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(TOKEN_URL, data=body)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.error("youtube_token_refresh_failed", status=exc.response.status_code, body=exc.response.text)
        raise YouTubeOAuthError(
            f"Refreshing the YouTube access token failed: {exc.response.text}. The connection may "
            "have been revoked on Google's side — reconnect via GET /api/youtube/oauth/login."
        ) from exc
    except httpx.HTTPError as exc:
        raise YouTubeOAuthError(f"Network error refreshing the YouTube access token: {exc}") from exc

    payload = response.json()
    credential.access_token = payload["access_token"]
    credential.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=payload.get("expires_in", 3600))
    db.commit()
    db.refresh(credential)
    log.info("youtube_token_refreshed", channel_id=credential.channel_id)


async def get_valid_access_token(db: Session) -> str:
    """
    Returns a currently-valid access token for the connected YouTube
    channel, transparently refreshing it first if it's expired (or close
    to it). This is what Phase 13's upload step calls before every request
    to the YouTube Data API.
    """
    credential = db.scalar(select(YouTubeCredential).limit(1))
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No YouTube account connected. Start at GET /api/youtube/oauth/login.",
        )

    if datetime.now(timezone.utc) >= credential.token_expiry - _EXPIRY_SAFETY_MARGIN:
        try:
            await _refresh_access_token(credential, db)
        except YouTubeOAuthError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return credential.access_token


def get_connection_status(db: Session) -> Optional[YouTubeCredential]:
    return db.scalar(select(YouTubeCredential).limit(1))


def disconnect(db: Session) -> None:
    """Forgets the stored credential. Does NOT revoke access on Google's side."""
    credential = db.scalar(select(YouTubeCredential).limit(1))
    if credential is not None:
        db.delete(credential)
        db.commit()
