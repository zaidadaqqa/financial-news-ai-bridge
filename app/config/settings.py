from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # News ingestion — RSS (primary, free, no auth)
    FJ_RSS_URL: str = Field(
        default="https://www.financialjuice.com/feed.ashx?xy=rss",
        description="FinancialJuice RSS feed URL",
    )
    RSS_POLL_INTERVAL: int = Field(
        default=30,
        description="Seconds between RSS polls",
    )
    # News ingestion — WebSocket Stream API (optional upgrade; set to enable real-time)
    FJ_STREAM_API_KEY: SecretStr | None = Field(
        None,
        description="FinancialJuice Stream API key for real-time feed (optional)",
    )

    # Telegram
    TELEGRAM_BOT_TOKEN: SecretStr = Field(..., description="Telegram Bot Token")
    TELEGRAM_CHAT_ID: str = Field(..., description="Telegram Chat ID to publish to")
    TELEGRAM_THREAD_ID: int | None = Field(
        None, description="Telegram topic/thread ID (optional)"
    )

    # AI Provider
    AI_PROVIDER: str = Field(default="openai")
    AI_MODEL: str = Field(default="gpt-4o-mini")
    AI_API_KEY: SecretStr = Field(..., description="API Key for the AI Provider")
    AI_BASE_URL: str | None = Field(None)

    # Database
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///data/news.db")

    # Application
    APP_ENV: str = Field(default="development")
    LOG_LEVEL: str = Field(default="INFO")
    TIMEZONE: str = Field(default="UTC")
    PORT: int = Field(default=8000)

    # Features
    ENABLE_TRANSLATION: bool = Field(default=True)
    ENABLE_AI_CACHE: bool = Field(default=True)
    ENABLE_MARKET_IMPACT: bool = Field(default=True)
    ENABLE_DUPLICATE_DETECTION: bool = Field(default=True)

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )


def get_settings() -> Settings:
    return Settings()  # type: ignore


settings = get_settings()
