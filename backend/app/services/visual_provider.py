"""
Phase 5: scene -> images.

Same pluggable-provider pattern as app/services/ai_provider.py (section 8):
an abstract VisualProvider that any concrete image source implements, and a
factory that picks the concrete class from VISUAL_PROVIDER in .env. Nothing
downstream (visual_service.py, the API) ever imports a concrete provider
directly — swapping providers is a one-line .env change.
"""

from abc import ABC, abstractmethod


class VisualProviderError(Exception):
    """Raised when a visual provider fails to produce an image after retries."""


class VisualProvider(ABC):
    """
    A visual provider turns one scene's visual_prompt into an image file on
    disk and returns the path it wrote to. Implementations are responsible
    for their own HTTP calls and retry policy (see providers/*.py — each
    wraps its request in tenacity.retry, mirroring the AI providers).
    """

    @abstractmethod
    async def generate_image(self, prompt: str, output_path: str) -> str:
        """
        Produce an image for `prompt` and write it to `output_path` (the
        full file path, including extension). Returns `output_path` on
        success. Raises VisualProviderError on any failure the caller
        should treat as this provider having failed for this scene.
        """
        raise NotImplementedError


def get_visual_provider() -> VisualProvider:
    """
    Factory selecting the concrete provider from settings.visual_provider.
    Imports are local to avoid loading every provider's dependencies (and
    requiring every provider's API key to be set) just to use one of them.
    """
    from app.config import settings

    if settings.visual_provider == "openai_images":
        from app.services.providers.openai_images_provider import OpenAIImagesProvider

        return OpenAIImagesProvider()
    if settings.visual_provider == "stock_pexels":
        from app.services.providers.pexels_provider import PexelsProvider

        return PexelsProvider()

    raise VisualProviderError(f"Unknown VISUAL_PROVIDER: {settings.visual_provider!r}")
