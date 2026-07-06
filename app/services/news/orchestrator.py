import asyncio

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
        """Main entry point for incoming Discord messages."""
        logger.info("Received new discord message", message_id=message_id)

        normalized = normalize_text(headline)
        if not normalized:
            logger.warning("Empty normalized headline, skipping", message_id=message_id)
            return

        news_hash = generate_news_hash(normalized, source_url)

        # Deduplication check
        existing = await self.news_repo.get_by_hash(news_hash)
        if existing:
            logger.info("Duplicate news detected, skipping", hash=news_hash)
            return

        # 1. Store initial event
        news = NewsEvent(
            discord_message_id=message_id,
            source_channel_id=channel_id,
            source_url=source_url,
            original_headline=headline,
            normalized_headline=normalized,
            hash=news_hash,
            status=NewsStatus.RECEIVED,
        )
        await self.news_repo.add(news)
        await self.news_repo.commit()

        # 2. Fast publish to Telegram (English)
        try:
            raw_text = TelegramFormatter.format_raw_english(news)
            tg_msg_id = await self.publisher.publish_message(raw_text)

            news.telegram_message_id = tg_msg_id
            news.status = NewsStatus.TELEGRAM_PENDING
            await self.news_repo.commit()

        except Exception as e:
            logger.error("Failed to publish initial telegram message", error=str(e))
            news.status = NewsStatus.FAILED
            await self.news_repo.commit()
            return

        # 3. Fire background AI processing (Async-first)
        # We launch this as a background task so we don't block the Discord event loop
        asyncio.create_task(self._process_ai_and_update(news.id))

    async def _process_ai_and_update(self, news_id: str) -> None:
        """Background task to run AI, validate, and edit the Telegram message."""
        news = await self.news_repo.get_by_id(news_id)
        if not news:
            return

        news.status = NewsStatus.AI_PENDING
        await self.news_repo.commit()

        try:
            # Generate Translation
            ai_data = await self.ai_provider.generate_financial_translation(
                news.normalized_headline
            )

            # Validate output constraints & numbers
            OutputValidator.validate_ai_output(news.normalized_headline, ai_data)

            # Update DB Model
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

            news.status = NewsStatus.AI_SUCCESS
            await self.news_repo.commit()

            # Format and Update Telegram
            if news.telegram_message_id:
                final_text = TelegramFormatter.format_premium_bilingual(news, ai_data)
                await self.publisher.edit_message(news.telegram_message_id, final_text)

                news.status = NewsStatus.PUBLISHED
                await self.news_repo.commit()

            logger.info("Successfully processed and published news", news_id=news.id)

        except (ValidationError, AIResponseError) as e:
            logger.error(
                "AI Validation/Generation failed", news_id=news.id, error=str(e)
            )
            news.status = NewsStatus.AI_FAILED
            await self.news_repo.commit()
        except Exception as e:
            logger.error(
                "Unexpected error in background processing",
                news_id=news.id,
                error=str(e),
            )
            news.status = NewsStatus.FAILED
            await self.news_repo.commit()
