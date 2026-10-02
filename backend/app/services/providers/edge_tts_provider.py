"""
Microsoft Edge TTS voice provider (free, no API key). Default TTS_PROVIDER.

Voice selection: TTS_DEFAULT_VOICE from .env is used for the project's
language when it matches TTS_DEFAULT_LANGUAGE; otherwise a small built-in
map picks a sensible default neural voice for the project's language
(so a project created with language="hi-IN" gets a Hindi voice
automatically, without any extra .env configuration — see LANGUAGE_VOICES
below). Unknown languages fall back to TTS_DEFAULT_VOICE.
"""

import os

import edge_tts

from app.config import settings
from app.services.tts_provider import TTSProvider, TTSProviderError
from app.utils.logger import get_logger

log = get_logger(__name__)

# A handful of common languages mapped to a good default neural voice.
# Extend this as more languages are needed — no code elsewhere depends on
# this list being exhaustive, since TTS_DEFAULT_VOICE is always the fallback.
LANGUAGE_VOICES = {
    "en-US": "en-US-JennyNeural",
    "en-GB": "en-GB-SoniaNeural",
    "hi-IN": "hi-IN-SwaraNeural",
    "es-ES": "es-ES-ElviraNeural",
    "fr-FR": "fr-FR-DeniseNeural",
}


class EdgeTTSProvider(TTSProvider):
    def _pick_voice(self, language: str) -> str:
        if language == settings.tts_default_language:
            return settings.tts_default_voice
        return LANGUAGE_VOICES.get(language, settings.tts_default_voice)

    async def synthesize(self, text: str, output_path: str, language: str) -> str:
        voice = self._pick_voice(language)
        try:
            communicate = edge_tts.Communicate(text, voice=voice)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            await communicate.save(output_path)
        except Exception as exc:  # noqa: BLE001 - edge_tts raises its own exception types
            log.error("edge_tts_failed", voice=voice, error=str(exc))
            raise TTSProviderError(f"Edge TTS synthesis failed (voice={voice}): {exc}") from exc

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise TTSProviderError(f"Edge TTS produced an empty file for voice={voice}")

        return output_path
