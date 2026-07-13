import asyncio
import re
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
from app.services.indicators.context import MacroContext, MacroContextReader
from app.services.indicators.engine import IndicatorMemoryEngine
from app.services.intelligence.engine import classify_news
from app.services.story.engine import StoryIntelligenceEngine
from app.services.story.models import StoryDecision
from app.services.telegram.publisher import TelegramPublisher
from app.services.validation.validator import OutputValidator
from app.utils.hashing import generate_news_hash
from app.utils.text import normalize_text

logger = get_logger(__name__)

MAX_RETRY_ATTEMPTS = 3

_BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_URL_RE = re.compile(r"https?://\S+")


def _sanitize_error(exc: Exception) -> str:
    """Error string safe to persist. httpx exceptions embed the full request
    URL — for Telegram that URL contains the bot token (found leaked in one
    production last_error row, 2026-07-13). Redact tokens and URLs before
    anything reaches the database."""
    text = f"{type(exc).__name__}: {str(exc)[:300]}"
    text = _BOT_TOKEN_RE.sub("bot<redacted>", text)
    text = _URL_RE.sub("<url>", text)
    return text[:120]


class NewsOrchestrator:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.news_repo = NewsRepository(session)
        self.publisher = TelegramPublisher()
        self.ai_provider = OpenAIProvider()
        self.story_engine = StoryIntelligenceEngine(session)
        self.indicator_memory = IndicatorMemoryEngine(session)
        self.macro_reader = MacroContextReader(session)

    async def process_message(
        self,
        source_id: str,
        source: str,
        headline: str,
        source_url: str | None = None,
    ) -> None:
        logger.info("Received news item", source=source, source_id=source_id)

        normalized = normalize_text(headline)
        if not normalized:
            logger.warning("Empty normalized headline, skipping", source_id=source_id)
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
            source_message_id=source_id,
            source=source,
            source_channel_id=None,
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
                "Duplicate source_message_id blocked by database constraint",
                source_id=source_id,
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
                source_id=source_id,
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
            # Deterministic, local, near-zero-latency classification — recomputed
            # here (not in the fast path) so it never touches initial-publish
            # latency. classify_news() is designed to never raise on its own
            # (internal safe-fallback), but it still runs inside this try block
            # so a record can never get stuck at AI_PENDING forever if something
            # truly unexpected happens — it fails the same way any other AI-stage
            # error does. See NEWS_INTELLIGENCE_ARCHITECTURE.md §3/§13.
            intelligence = classify_news(news.normalized_headline, news.source_url)
            logger.info(
                "News classified",
                category=intelligence.category,
                urgency=intelligence.urgency,
                is_fallback=intelligence.is_fallback,
            )
            logger.debug(
                "News classification detail",
                country=intelligence.country,
                currency=intelligence.currency,
                central_bank=intelligence.central_bank,
                economic_event=intelligence.economic_event,
                surprise=intelligence.surprise_direction,
                reasons=intelligence.classification_reasons,
            )

            # Story Intelligence (Phase 3) — strictly background, isolated:
            # a failure here logs one warning and the item proceeds exactly
            # as Phase 2 with no story context. It can never mark the item
            # FAILED and never touches the already-sent initial message.
            # See STORY_INTELLIGENCE_ARCHITECTURE.md §9/§14.
            story_decision: StoryDecision | None = None
            try:
                story_decision = await self.story_engine.process(news, intelligence)
            except Exception as story_err:
                await self.session.rollback()
                logger.warning(
                    "Story intelligence failed, continuing without story context",
                    news_id=news.id[:8],
                    error_type=type(story_err).__name__,
                )

            # Indicator Memory (Phase 4A) — completely dark: silently
            # accumulates the platform's historical database of economic
            # prints. Writes only; influences nothing. Isolated: failure
            # logs one warning and the item proceeds identically.
            try:
                await self.indicator_memory.record(news, intelligence)
            except Exception as ind_err:
                await self.session.rollback()
                logger.warning(
                    "Indicator memory failed, continuing",
                    news_id=news.id[:8],
                    error_type=type(ind_err).__name__,
                )

            # Macro Context (Phase 4B) — deterministic, read-only historical
            # facts from Indicator Memory, handed to the AI as authoritative
            # context. Isolated: any failure means no context and the item
            # proceeds exactly as Phase 4A. Never touches the formatter.
            macro_context: MacroContext | None = None
            try:
                macro_context = await self.macro_reader.read(news, intelligence)
            except Exception as macro_err:
                await self.session.rollback()
                logger.warning(
                    "Macro context failed, continuing without it",
                    news_id=news.id[:8],
                    error_type=type(macro_err).__name__,
                )

            ai_data = await self.ai_provider.generate_financial_translation(
                news.normalized_headline, intelligence, story_decision, macro_context
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
                final_text = TelegramFormatter.format_premium_bilingual(
                    news, ai_data, intelligence, story_decision
                )
                await self.publisher.edit_message(news.telegram_message_id, final_text)

                news.status = NewsStatus.PUBLISHED
                await self.news_repo.commit()

                # Promote this item to its story's latest published
                # development — the prior-context for the next related item.
                # Isolated: failure logs a warning, the item stays PUBLISHED.
                if story_decision is not None:
                    try:
                        await self.story_engine.record_published(story_decision, news)
                    except Exception as story_err:
                        await self.session.rollback()
                        logger.warning(
                            "Story publish-record failed",
                            news_id=news.id[:8],
                            error_type=type(story_err).__name__,
                        )

            logger.info(
                "Successfully processed and published news", news_id=news.id[:8]
            )

        except (ValidationError, AIResponseError) as e:
            safe_err = _sanitize_error(e)
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
            safe_err = _sanitize_error(e)
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
