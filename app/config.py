"""Application settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("saathi.config")

Environment = Literal["production", "development", "test"]


class ConfigError(RuntimeError):
    """Raised when the configuration is unsafe or incomplete for the chosen environment."""


class Settings(BaseSettings):
    """Typed runtime configuration. Secrets never have defaults."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Environment = "development"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.6-flash"
    # 0 disables "thinking" on Flash models for lower latency; set to -1 to omit the setting.
    gemini_thinking_budget: int = 0
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    ai_rate_limit_per_minute: int = Field(default=12, ge=1, le=1000)
    ai_cache_size: int = Field(default=256, ge=0, le=10_000)
    ai_cache_ttl_seconds: int = Field(default=3600, ge=0)
    max_body_bytes: int = Field(default=1_000_000, ge=1024)
    allowed_origins: list[str] = Field(default_factory=list)
    log_level: str = "INFO"

    @property
    def ai_configured(self) -> bool:
        """True when a non-empty Gemini API key is available."""
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value().strip())


def validate_settings(settings: Settings) -> None:
    """Fail fast when production is started without the Gemini key."""
    if settings.env == "production" and not settings.ai_configured:
        logger.critical("GEMINI_API_KEY is missing. Refusing to start in production mode.")
        raise ConfigError("GEMINI_API_KEY must be set when ENV=production.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (read once)."""
    return Settings()
