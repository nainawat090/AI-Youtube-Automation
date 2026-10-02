"""
Gemini implementation of AIProvider. Uses Google's generateContent REST API
directly with responseMimeType=application/json for structured output.
"""

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.ai_provider import AIProvider, AIProviderError
from app.utils.logger import get_logger

log = get_logger(__name__)

GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider(AIProvider):
    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise AIProviderError(
                "GEMINI_API_KEY is not set. Add it to .env or switch AI_PROVIDER."
            )
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_model

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=settings.retry_backoff_base_seconds),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        url = GEMINI_URL_TEMPLATE.format(model=self.model)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.7,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    url, params={"key": self.api_key}, json=payload
                )
                resp.raise_for_status()
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except httpx.HTTPStatusError as exc:
            log.error("gemini_request_failed", status=exc.response.status_code, body=exc.response.text[:500])
            raise
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected Gemini response shape: {exc}") from exc
