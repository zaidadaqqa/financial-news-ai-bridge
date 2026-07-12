"""Indicator Memory engine (Phase 4A) — the silent writer.

Completely dark: returns nothing the pipeline uses, influences no message,
no AI prompt, no formatter. The orchestrator wraps the single call in its
own try/except; failure logs one WARNING and the item proceeds exactly as
before. Numbers come from the frozen intelligence engine's deterministic
extraction (not the AI), so history accumulates even for items whose AI
stage later fails.
"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.log.logger import get_logger
from app.models.indicator import IndicatorPrint, IndicatorSeries
from app.models.news import NewsEvent, generate_uuid
from app.repositories.indicator_repository import IndicatorRepository
from app.services.indicators.parser import identify_series
from app.services.intelligence.models import NewsIntelligenceResult
from app.services.intelligence.rules import parse_economic_value
from app.services.story.rules import has_correction_marker

logger = get_logger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _dec_str(raw: str | None) -> str | None:
    """Canonical plain-notation Decimal string: normalize() strips
    arithmetic artifacts (a K/M/B multiplication leaves a trailing ".0" in
    str()), and format(..., "f") avoids normalize()'s scientific notation —
    so "4.1M" stores as "4100000" and "-0.2%" as "-0.2", identically
    regardless of the published spelling. The published form is preserved
    separately in the *_raw columns."""
    if raw is None:
        return None
    value = parse_economic_value(raw)
    if value is None:
        return None
    return format(value.normalize(), "f")


class IndicatorMemoryEngine:
    def __init__(self, session: AsyncSession):
        self.repo = IndicatorRepository(session)

    async def record(
        self, news: NewsEvent, intelligence: NewsIntelligenceResult
    ) -> None:
        """Persist one economic print, keyed when identification is
        deterministic, honestly unkeyed otherwise. Idempotent: reprocessing
        the same news item writes nothing (soft check + UNIQUE(news_id)
        backstop)."""
        if intelligence.actual is None:
            return  # not a structured print — nothing to store

        if await self.repo.get_print_by_news_id(news.id) is not None:
            return  # restart / duplicate task — already recorded

        identity, unkeyed_reason = identify_series(
            news.normalized_headline, intelligence
        )
        print_at = _ensure_utc(news.created_at or datetime.now(UTC))

        series: IndicatorSeries | None = None
        if identity is not None:
            series = await self.repo.get_series_by_key(identity.canonical_key)
            if series is None:
                series = IndicatorSeries(
                    id=generate_uuid(),
                    canonical_key=identity.canonical_key,
                    country=identity.country,
                    economic_event=identity.economic_event,
                    variant=identity.variant,
                    unit_class=identity.unit_class,
                    print_count=0,
                    unit_mismatch_count=0,
                    unknown_surprise_count=0,
                    revision_count=0,
                    first_print_at=print_at,
                )
                self.repo.add(series)

        # Quality counters — stored facts for the engineering-only score.
        surprise = intelligence.surprise_direction.value
        forecast_dec = _dec_str(intelligence.forecast)
        is_revision = has_correction_marker(news.normalized_headline)
        revision_of: str | None = None
        if series is not None:
            series.print_count += 1
            series.last_print_at = print_at
            if surprise == "UNKNOWN" and forecast_dec is not None:
                # A PARSEABLE forecast existed yet the frozen comparison
                # refused — a genuine unit-mismatch data-quality signal. A
                # dash placeholder ("Forecast -") parses to None and lands
                # in the benign bucket below instead.
                series.unit_mismatch_count += 1
            elif surprise == "UNKNOWN":
                series.unknown_surprise_count += 1
            if is_revision:
                series.revision_count += 1
                prior = await self.repo.latest_print(series.id)
                if prior is not None:
                    revision_of = prior.id

        record = IndicatorPrint(
            series_id=series.id if series is not None else None,
            news_id=news.id,
            canonical_key=identity.canonical_key if identity else None,
            unkeyed_reason=unkeyed_reason,
            actual_raw=intelligence.actual,
            forecast_raw=intelligence.forecast,
            previous_raw=intelligence.previous,
            actual_dec=_dec_str(intelligence.actual),
            forecast_dec=forecast_dec,
            previous_dec=_dec_str(intelligence.previous),
            surprise_direction=surprise,
            revision_of=revision_of,
            print_at=print_at,
        )
        self.repo.add(record)
        try:
            await self.repo.commit()
        except IntegrityError:
            # UNIQUE(news_id) backstop — someone recorded it first.
            await self.repo.rollback()
            return

        logger.debug(
            "Indicator print recorded",
            keyed=identity is not None,
            series=(identity.canonical_key if identity else unkeyed_reason),
        )
