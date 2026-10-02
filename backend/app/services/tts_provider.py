"""
Phase 6: scene -> voice narration.

Same pluggable-provider pattern as ai_provider.py and visual_provider.py: an
abstract TTSProvider any concrete voice engine implements, and a factory
that picks the concrete class from TTS_PROVIDER in .env. Nothing downstream
(tts_service.py, the API) ever imports a concrete provider directly.
"""

from abc import ABC, abstractmethod


class TTSProviderError(Exception):
    """Raised when a TTS provider fails to produce narration audio."""


class TTSProvider(ABC):
    """
    A TTS provider turns one scene's narration text into a spoken-audio file
    on disk and returns the path it wrote to. `language` is the project's
    BCP-47 locale (e.g. "en-US", "hi-IN") so a provider can pick an
    appropriate voice automatically when the project isn't English.
    """

    @abstractmethod
    async def synthesize(self, text: str, output_path: str, language: str) -> str:
        """
        Synthesize `text` as speech and write it to `output_path` (the full
        file path, including extension). Returns `output_path` on success.
        Raises TTSProviderError on any failure the caller should treat as
        this provider having failed for this scene.
        """
        raise NotImplementedError


def get_tts_provider() -> TTSProvider:
    """
    Factory selecting the concrete provider from settings.tts_provider.
    Imports are local so only the chosen provider's dependencies/API key
    are required.
    """
    from app.config import settings

    if settings.tts_provider == "edge_tts":
        from app.services.providers.edge_tts_provider import EdgeTTSProvider

        return EdgeTTSProvider()
    if settings.tts_provider == "elevenlabs":
        from app.services.providers.elevenlabs_provider import ElevenLabsProvider

        return ElevenLabsProvider()
    if settings.tts_provider == "openai_tts":
        from app.services.providers.openai_tts_provider import OpenAITTSProvider

        return OpenAITTSProvider()

    raise TTSProviderError(f"Unknown TTS_PROVIDER: {settings.tts_provider!r}")
