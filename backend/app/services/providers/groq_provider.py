"""
Groq implementation of AIProvider. Groq's API is OpenAI-compatible, so the
request/response shape mirrors OpenAIProvider almost exactly — kept as a
separate class (rather than parameterizing one class) so each provider's
quirks (base URL, auth header, model name, JSON-mode support) stay isolated
and easy to change independently as each API evolves.
"""

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.ai_provider import AIProvider, AIProviderError
from app.utils.logger import get_logger

log = get_logger(__name__)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(AIProvider):
    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise AIProviderError(
                "GROQ_API_KEY is not set. Add it to .env or switch AI_PROVIDER."
            )
        self.api_key = settings.groq_api_key
        self.model = settings.groq_model

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
                resp = await client.post(GROQ_CHAT_URL, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            log.error("groq_request_failed", status=exc.response.status_code, body=exc.response.text[:500])
            raise
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected Groq response shape: {exc}") from exc
