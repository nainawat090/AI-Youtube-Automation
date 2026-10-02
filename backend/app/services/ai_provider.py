"""
AIProvider: the abstraction every LLM backend implements, so the rest of the
app (script_service.py) never talks to OpenAI/Groq/Gemini directly. Swapping
providers is a one-line env var change (AI_PROVIDER), per section 3.
"""

from abc import ABC, abstractmethod


class AIProviderError(Exception):
    """Raised when a provider call fails after all retries, or returns something unusable."""


class AIProvider(ABC):
    """Every provider takes a system + user prompt and must return raw text
    (expected to be JSON — validation happens one layer up in script_service)."""

    @abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return the raw text completion. Raises AIProviderError on failure."""
        raise NotImplementedError


def get_ai_provider() -> AIProvider:
    """Factory: returns the configured provider based on AI_PROVIDER env var."""
    from app.config import settings
    from app.services.providers.gemini_provider import GeminiProvider
    from app.services.providers.groq_provider import GroqProvider
    from app.services.providers.openai_provider import OpenAIProvider

    provider_map = {
        "openai": OpenAIProvider,
        "groq": GroqProvider,
        "gemini": GeminiProvider,
    }
    provider_cls = provider_map.get(settings.ai_provider)
    if provider_cls is None:
        raise AIProviderError(
            f"Unknown AI_PROVIDER '{settings.ai_provider}'. Must be one of: {list(provider_map)}"
        )
    return provider_cls()
