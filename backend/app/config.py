"""
Centralized application configuration.

All environment variables are read exactly once, here, via pydantic-settings.
No other module should call os.environ / os.getenv directly — import `settings`
from this module instead. This keeps secrets out of business logic and makes
config testable/mockable.
"""

from functools import lru_cache
from typing import List, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- App ----------
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = True
    app_secret_key: str = "change-me"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    # ---------- Database ----------
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_youtube_automation"

    # ---------- Redis / Celery ----------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ---------- LLM Provider ----------
    ai_provider: Literal["openai", "groq", "gemini"] = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"

    # ---------- Visual Provider ----------
    visual_provider: Literal["openai_images", "stock_pexels"] = "openai_images"
    stock_image_api_key: str = ""

    # ---------- TTS Provider ----------
    tts_provider: Literal["edge_tts", "elevenlabs", "openai_tts"] = "edge_tts"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    tts_default_voice: str = "en-US-JennyNeural"
    tts_default_language: str = "en-US"

    # ---------- YouTube ----------
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = "http://localhost:8000/api/youtube/oauth/callback"
    youtube_default_privacy_status: Literal["private", "unlisted", "public"] = "private"
    youtube_auto_publish: bool = False

    # ---------- Storage ----------
    storage_provider: Literal["local", "s3"] = "local"
    local_storage_path: str = "./assets"
    output_path: str = "./output"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_bucket_name: str = ""
    aws_region: str = "us-east-1"

    # ---------- Notifications ----------
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ---------- n8n (Phase 14) ----------
    # Inbound: lets an n8n workflow start a new video with one HTTP call
    # (POST /api/webhooks/n8n/trigger) instead of you opening Swagger. If
    # left blank, the endpoint still works but logs a warning on every call
    # — acceptable for local/dev use, but set this before exposing the
    # backend beyond your own machine.
    n8n_inbound_webhook_secret: str = ""
    # Outbound: the backend calls this n8n Webhook-node URL at key
    # milestones (pipeline ready for review, pipeline failed, upload
    # succeeded, upload failed) so an n8n workflow can react — e.g. post a
    # Slack/Telegram message with a review link. Left blank, no outbound
    # calls are made at all.
    n8n_outbound_webhook_url: str = ""
    # Optional shared secret sent as the X-Webhook-Secret header on those
    # outbound calls, so the n8n workflow can verify the call really came
    # from this backend.
    n8n_outbound_webhook_secret: str = ""

    # ---------- Retry config ----------
    max_retries: int = 3
    retry_backoff_base_seconds: int = 2

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — env is only parsed once per process."""
    return Settings()


settings = get_settings()
