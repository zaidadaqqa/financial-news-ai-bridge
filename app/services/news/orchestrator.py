import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import NewsStatus
from app.exceptions.custom_exceptions import AIResponseError, ValidationError
from app.log.logger import get_logger
from app.models.news import NewsEvent
from app.repositories.news_repository import NewsRepository
from app.services.ai.openai_provider import OpenAIProvider
from app.services.formatting.telegram_formatter import TelegramFormatter
from app.services.telegram.publisher import TelegramPublisher
from app.services.validation.validator import OutputValidator
from app.utils.hashing import generate_news_hash
from app.utils.text import normalize_text

logger = get_logger(__name__)

MAX_RETRY_ATTEMPTS = 3


class NewsOrchestrator:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.news_repo = NewsRepository(session)
        self.publisher = TelegramPublisher()
        self.ai_provider = OpenAIProvider()

    async def process_discord_message(
        self,
        message_id: str,
        channel_id: str,
        headline: str,
        source_url: str | None = None,
    ) -> None:
        logger.info("Received new discord message", message_id=message_id)

        normalized = normalize_text(headline)
        if not normalized:
            logger.warning("Empty normalized headline, skipping", message_id=message_id)
            return

        news_hash = generate_news_hash(normalized, source_url)

        existing = await self.news_repo.get_by_hash(news_hash)
        if existing:
            logger.info(
                "Duplicate news detected by hash, skipping",
                hash=news_hash[:16],
                existing_id=existing.id[:8],
            )
            return

        news = NewsEvent(
            discord_message_id=message_id,
            source_channel_id=channel_id,
            source_url=source_url,
            original_headline=headline,
            normalized_headline=normalized,
            hash=news_hash,
            status=NewsStatus.RECEIVED,
        )

        try:
            await self.news_repo.add(news)
            await self.news_repo.commit()
        except IntegrityError:
            await self.session.rollback()
            logger.info(
                "Duplicate discord_message_id blocked by database constraint",
                message_id=message_id,
            )
            return

        try:
            raw_text = TelegramFormatter.format_raw_english(news)
            tg_msg_id = await self.publisher.publish_message(raw_text)

            news.telegram_message_id = tg_msg_id
            news.status = NewsStatus.TELEGRAM_PENDING
            await self.news_repo.commit()

        except Exception as e:
            safe_err = type(e).__name__
            logger.error(
                "Failed to publish initial telegram message",
                error=safe_err,
                message_id=message_id,
            )
            news.status = NewsStatus.FAILED
            news.last_error = safe_err
            news.last_error_at = datetime.now(UTC)
            await self.news_repo.commit()
            return

        asyncio.create_task(self._process_ai_and_update(news.id))

    async def _process_ai_and_update(self, news_id: str) -> None:
        news = await self.news_repo.get_by_id(news_id)
        if not news:
            return

        news.status = NewsStatus.AI_PENDING
        await self.news_repo.commit()

        try:
            ai_data = await self.ai_provider.generate_financial_translation(
                news.normalized_headline
            )

            OutputValidator.validate_ai_output(news.normalized_headline, ai_data)

            news.translated_headline = ai_data.get("translation_ar")
            news.summary_ar = ai_data.get("summary_ar")
            news.category = ai_data.get("category")
            news.importance = (
                int(ai_data.get("importance", 2)) if ai_data.get("importance") else None
            )
            news.confidence = (
                float(ai_data.get("confidence", 0.0))
                if ai_data.get("confidence")
                else None
            )
            news.market_bias = ai_data.get("market_bias")
            news.impact = ai_data.get("impact")
            news.affected_assets = ai_data.get("affected_assets", [])
            news.actual = str(ai_data["actual"]) if ai_data.get("actual") else None
            news.forecast = (
                str(ai_data["forecast"]) if ai_data.get("forecast") else None
            )
            news.previous = (
                str(ai_data["previous"]) if ai_data.get("previous") else None
            )
            news.company = ai_data.get("company")
            news.ticker = ai_data.get("ticker")
            news.currency = ai_data.get("currency")

            news.status = NewsStatus.AI_SUCCESS
            await self.news_repo.commit()

            if news.telegram_message_id:
                final_text = TelegramFormatter.format_premium_bilingual(news, ai_data)
                await self.publisher.edit_message(news.telegram_message_id, final_text)

                news.status = NewsStatus.PUBLISHED
                await self.news_repo.commit()

            logger.info(
                "Successfully processed and published news", news_id=news.id[:8]
            )

        except (ValidationError, AIResponseError) as e:
            safe_err = f"{type(e).__name__}: {str(e)[:120]}"
            logger.error(
                "AI validation/generation failed",
                news_id=news.id[:8],
                error_type=type(e).__name__,
            )
            news.status = NewsStatus.AI_FAILED
            news.retry_count = (news.retry_count or 0) + 1
            news.last_error = safe_err
            news.last_error_at = datetime.now(UTC)
            await self.news_repo.commit()

        except Exception as e:
            safe_err = f"{type(e).__name__}: {str(e)[:120]}"
            logger.error(
                "Unexpected error in background processing",
                news_id=news.id[:8],
                error_type=type(e).__name__,
            )
            news.status = NewsStatus.FAILED
            news.retry_count = (news.retry_count or 0) + 1
            news.last_error = safe_err
            news.last_error_at = datetime.now(UTC)
            await self.news_repo.commit()

    async def recover_interrupted_records(self) -> None:
        """Re-queue records stuck in transient states after a restart."""
        from app.database.connection import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(NewsEvent).where(
                    NewsEvent.status.in_(
                        [NewsStatus.AI_PENDING, NewsStatus.TELEGRAM_PENDING]
                    ),
                    NewsEvent.retry_count < MAX_RETRY_ATTEMPTS,
                )
            )
            stuck = result.scalars().all()

            if not stuck:
                return

            logger.info("Recovering interrupted records", count=len(stuck))
            for news in stuck:
                if (
                    news.status == NewsStatus.TELEGRAM_PENDING
                    and news.telegram_message_id
                ):
                    logger.info(
                        "Retrying AI processing for stuck record",
                        news_id=news.id[:8],
                    )
                    asyncio.create_task(self._process_ai_and_update(news.id))
                else:
                    news.status = NewsStatus.FAILED
                    news.last_error = "Interrupted during startup recovery"
                    news.last_error_at = datetime.now(UTC)

            await session.commit()
