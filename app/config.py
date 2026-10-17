"""Application settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
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
    ai_rate_limit_per_minute: int = Field(default=12, ge=1, le=1000)  # cost units per client
    ai_global_per_minute: int = Field(default=120, ge=1, le=100_000)  # cost units per instance
    api_rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)  # all /api requests per client
    ai_cache_size: int = Field(default=256, ge=0, le=10_000)
    ai_cache_ttl_seconds: int = Field(default=3600, ge=0)
    max_body_bytes: int = Field(default=1_000_000, ge=1024)
    allowed_origins: list[str] = Field(default_factory=list)
    log_level: str = "INFO"
    # Vercel sets VERCEL=1. Its edge overwrites X-Forwarded-For / X-Real-IP, so they can be trusted there.
    vercel: bool = False
    trust_proxy: bool = False

    @property
    def ai_configured(self) -> bool:
        """True when a non-empty Gemini API key is available."""
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value().strip())

    @property
    def trust_proxy_headers(self) -> bool:
        """Use proxy-supplied client IPs only behind a proxy that sets them (Vercel, or opted in)."""
        return self.vercel or self.trust_proxy


def validate_settings(settings: Settings) -> None:
    """Fail fast when production is started without the Gemini key."""
    if settings.env == "production" and not settings.ai_configured:
        logger.critical("GEMINI_API_KEY is missing. Refusing to start in production mode.")
        raise ConfigError("GEMINI_API_KEY must be set when ENV=production.")


def load_settings() -> Settings:
    """Read settings; a bad value is reported by field name only, never by value (no secret leaks)."""
    try:
        return Settings()
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()})
        logger.critical("Invalid configuration for: %s", ", ".join(fields))
        raise ConfigError(f"Invalid configuration for: {', '.join(fields)}") from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (read once)."""
    return load_settings()
