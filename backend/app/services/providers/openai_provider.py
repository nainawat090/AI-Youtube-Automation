"""
OpenAI implementation of AIProvider. Uses httpx directly (no SDK dependency)
against the Chat Completions API with JSON mode, since the app only needs
one call shape and an SDK would be a heavier dependency for that.
"""

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.ai_provider import AIProvider, AIProviderError
from app.utils.logger import get_logger

log = get_logger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(AIProvider):
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise AIProviderError(
                "OPENAI_API_KEY is not set. Add it to .env or switch AI_PROVIDER."
            )
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=settings.retry_backoff_base_seconds),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        }
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(OPENAI_CHAT_URL, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            log.error("openai_request_failed", status=exc.response.status_code, body=exc.response.text[:500])
            raise
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected OpenAI response shape: {exc}") from exc
