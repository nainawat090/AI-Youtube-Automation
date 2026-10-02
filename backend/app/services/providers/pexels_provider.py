"""
Pexels stock-photo visual provider (free tier, no image generation cost).
Searches Pexels for a photo matching the scene's visual_prompt and downloads
the best match. Used when VISUAL_PROVIDER=stock_pexels and STOCK_IMAGE_API_KEY
is a Pexels API key (https://www.pexels.com/api/ — free to obtain).
"""

import os

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.visual_provider import VisualProvider, VisualProviderError
from app.utils.logger import get_logger

log = get_logger(__name__)

SEARCH_URL = "https://api.pexels.com/v1/search"


class PexelsProvider(VisualProvider):
    def __init__(self) -> None:
        if not settings.stock_image_api_key:
            raise VisualProviderError("STOCK_IMAGE_API_KEY is not set but VISUAL_PROVIDER=stock_pexels")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _search_photo_url(self, prompt: str) -> str:
        headers = {"Authorization": settings.stock_image_api_key}
        params = {"query": prompt, "per_page": 1, "orientation": "landscape"}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(SEARCH_URL, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

        photos = data.get("photos", [])
        if not photos:
            raise VisualProviderError(f"No Pexels results for prompt: {prompt!r}")
        return photos[0]["src"]["large2x"]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _download_image(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def generate_image(self, prompt: str, output_path: str) -> str:
        try:
            photo_url = await self._search_photo_url(prompt)
            image_bytes = await self._download_image(photo_url)
        except VisualProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            log.error("pexels_request_failed", status=exc.response.status_code, body=exc.response.text)
            raise VisualProviderError(
                f"Pexels request failed: {exc.response.status_code} {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            log.error("pexels_network_error", error=str(exc))
            raise VisualProviderError(f"Pexels network error: {exc}") from exc

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        return output_path
