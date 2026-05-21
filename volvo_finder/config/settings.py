"""Application settings using pydantic-settings."""
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///volvo_finder.db"
    CACHE_DIR: str = ".cache"
    CACHE_TTL_HOURS: int = 6
    REQUEST_DELAY_SECONDS: float = 1.0
    USER_AGENT: str = "VolvoFinder/1.0 (personal research bot)"
    LOG_LEVEL: str = "INFO"

    # Telegram notifications (optional)
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # Email notifications (optional)
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    NOTIFY_EMAIL: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


# Singleton instance
settings = Settings()
