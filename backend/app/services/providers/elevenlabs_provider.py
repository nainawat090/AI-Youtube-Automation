"""
ElevenLabs voice provider (paid, higher-quality/cloned voices). Raw httpx
against the REST API — no official SDK, same convention as the other
providers in this folder.
"""

import os

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.tts_provider import TTSProvider, TTSProviderError
from app.utils.logger import get_logger

log = get_logger(__name__)


class ElevenLabsProvider(TTSProvider):
    def __init__(self) -> None:
        if not settings.elevenlabs_api_key:
            raise TTSProviderError("ELEVENLABS_API_KEY is not set but TTS_PROVIDER=elevenlabs")
        if not settings.elevenlabs_voice_id:
            raise TTSProviderError("ELEVENLABS_VOICE_ID is not set but TTS_PROVIDER=elevenlabs")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _request_audio(self, text: str) -> bytes:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}"
        headers = {
            "xi-api-key": settings.elevenlabs_api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        body = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.content

    async def synthesize(self, text: str, output_path: str, language: str) -> str:
        try:
            audio_bytes = await self._request_audio(text)
        except httpx.HTTPStatusError as exc:
            log.error("elevenlabs_request_failed", status=exc.response.status_code, body=exc.response.text)
            raise TTSProviderError(
                f"ElevenLabs request failed: {exc.response.status_code} {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            log.error("elevenlabs_network_error", error=str(exc))
            raise TTSProviderError(f"ElevenLabs network error: {exc}") from exc

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)
        return output_path
