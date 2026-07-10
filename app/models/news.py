import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.constants.enums import NewsStatus
from app.models.base import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class NewsEvent(Base):
    __tablename__ = "news"
    __table_args__ = (
        UniqueConstraint("discord_message_id", name="uq_news_discord_message_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    discord_message_id: Mapped[str] = mapped_column(String, nullable=False)
    telegram_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_channel_id: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)

    original_headline: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_headline: Mapped[str] = mapped_column(Text, nullable=False)
    translated_headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_ar: Mapped[str | None] = mapped_column(Text, nullable=True)

    category: Mapped[str | None] = mapped_column(String, nullable=True)
    importance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_bias: Mapped[str | None] = mapped_column(String, nullable=True)
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)

    affected_assets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    actual: Mapped[str | None] = mapped_column(String, nullable=True)
    forecast: Mapped[str | None] = mapped_column(String, nullable=True)
    previous: Mapped[str | None] = mapped_column(String, nullable=True)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)

    status: Mapped[NewsStatus] = mapped_column(
        SAEnum(NewsStatus), default=NewsStatus.RECEIVED, nullable=False
    )
    hash: Mapped[str] = mapped_column(String, index=True, nullable=False)

    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AICache(Base):
    __tablename__ = "ai_cache"

    hash: Mapped[str] = mapped_column(String, primary_key=True)
    request: Mapped[dict] = mapped_column(JSON, nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ProcessingLog(Base):
    __tablename__ = "processing_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    news_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    step: Mapped[str] = mapped_column(String, nullable=False)
    execution_time_ms: Mapped[float] = mapped_column(Float, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
