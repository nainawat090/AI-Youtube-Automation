"""
OpenAI TTS voice provider. Raw httpx against the REST API — no official
SDK, same convention as the other providers in this folder.
"""

import os

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.tts_provider import TTSProvider, TTSProviderError
from app.utils.logger import get_logger

log = get_logger(__name__)

SPEECH_URL = "https://api.openai.com/v1/audio/speech"


class OpenAITTSProvider(TTSProvider):
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise TTSProviderError("OPENAI_API_KEY is not set but TTS_PROVIDER=openai_tts")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _request_audio(self, text: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {"model": "tts-1", "voice": "alloy", "input": text, "response_format": "mp3"}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(SPEECH_URL, headers=headers, json=body)
            response.raise_for_status()
            return response.content

    async def synthesize(self, text: str, output_path: str, language: str) -> str:
        try:
            audio_bytes = await self._request_audio(text)
        except httpx.HTTPStatusError as exc:
            log.error("openai_tts_request_failed", status=exc.response.status_code, body=exc.response.text)
            raise TTSProviderError(
                f"OpenAI TTS request failed: {exc.response.status_code} {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            log.error("openai_tts_network_error", error=str(exc))
            raise TTSProviderError(f"OpenAI TTS network error: {exc}") from exc

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)
        return output_path
