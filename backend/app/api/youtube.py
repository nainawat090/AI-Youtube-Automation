"""
YouTube OAuth API (Phase 12). Connects this deployment to ONE YouTube
channel via the standard OAuth 2.0 authorization-code flow, so Phase 13
can upload videos on that channel's behalf. See
app/services/youtube_oauth_service.py for the actual token exchange logic
— this module is just the thin HTTP endpoints around it.
"""

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.youtube import YouTubeConnectionStatus
from app.services.youtube_oauth_service import (
    YouTubeOAuthError,
    build_authorization_url,
    disconnect,
    exchange_code_for_tokens,
    get_connection_status,
)
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/youtube", tags=["youtube"])


@router.get("/oauth/login")
def youtube_oauth_login() -> dict:
    """
    Step 1: returns the Google consent-screen URL. Open it in a real
    browser (not from Swagger — this is a Google-hosted page the account
    owner has to approve by hand), sign in, and approve access. Google
    then redirects to GET /oauth/callback on its own.
    """
    try:
        # `state` isn't validated against a session yet (there's no
        # session/cookie layer in this single-operator app), but is still
        # sent as a baseline CSRF mitigation per the OAuth spec.
        state = secrets.token_urlsafe(16)
        url = build_authorization_url(state)
    except YouTubeOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"authorization_url": url}


@router.get("/oauth/callback", response_class=HTMLResponse)
async def youtube_oauth_callback(
    code: str = Query(...),
    error: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> str:
    """
    Step 2: Google's redirect target after the user approves — exchanges
    the authorization code for tokens and stores them. Returns a plain
    HTML confirmation page (a browser lands here, not Swagger).
    """
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Google returned an error: {error}")

    try:
        credential = await exchange_code_for_tokens(code, db)
    except YouTubeOAuthError as exc:
        log.error("youtube_oauth_callback_failed", error=str(exc))
        return f"<h1>YouTube connection failed</h1><p>{exc}</p>"

    return (
        "<h1>YouTube connected</h1>"
        f"<p>Channel: <b>{credential.channel_title or 'Unknown'}</b></p>"
        "<p>You can close this tab.</p>"
    )


@router.get("/oauth/status", response_model=YouTubeConnectionStatus)
def youtube_oauth_status(db: Session = Depends(get_db)) -> YouTubeConnectionStatus:
    """Whether a YouTube channel is connected, and which one — no tokens exposed."""
    credential = get_connection_status(db)
    if credential is None:
        return YouTubeConnectionStatus(connected=False)
    return YouTubeConnectionStatus(
        connected=True,
        channel_id=credential.channel_id,
        channel_title=credential.channel_title,
        token_expiry=credential.token_expiry,
    )


@router.delete("/oauth/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def youtube_oauth_disconnect(db: Session = Depends(get_db)) -> None:
    """Forgets the stored credential. Does NOT revoke access on Google's side."""
    disconnect(db)
