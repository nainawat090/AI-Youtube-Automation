"""
OpenAI Images (DALL-E 3) visual provider. Raw httpx against the REST API —
no official SDK, same convention as the other providers in this folder.
"""

import base64
import os

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.visual_provider import VisualProvider, VisualProviderError
from app.utils.logger import get_logger

log = get_logger(__name__)

IMAGES_URL = "https://api.openai.com/v1/images/generations"


class OpenAIImagesProvider(VisualProvider):
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise VisualProviderError("OPENAI_API_KEY is not set but VISUAL_PROVIDER=openai_images")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _request_image(self, prompt: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": "dall-e-3",
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "response_format": "b64_json",
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(IMAGES_URL, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            b64_data = data["data"][0]["b64_json"]
            return base64.b64decode(b64_data)

    async def generate_image(self, prompt: str, output_path: str) -> str:
        try:
            image_bytes = await self._request_image(prompt)
        except httpx.HTTPStatusError as exc:
            body = exc.response.text
            log.error("openai_images_request_failed", status=exc.response.status_code, body=body)
            raise VisualProviderError(
                f"OpenAI Images request failed: {exc.response.status_code} {body}"
            ) from exc
        except httpx.HTTPError as exc:
            log.error("openai_images_network_error", error=str(exc))
            raise VisualProviderError(f"OpenAI Images network error: {exc}") from exc

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        return output_path
