"""Bounded historical backfill for Indicator Memory (Phase 4B, owner-approved).

Replays existing validated news records through the EXACT live accumulation
path (frozen classify_news → IndicatorMemoryEngine.record) so backfilled
history is indistinguishable from naturally accumulated history: same
canonical identity, same honest unkeyed handling, same idempotency backbone
(UNIQUE(news_id) — re-running is always safe).

Rules (permanent):
- chronological order, so print_at ordering and revision linking behave as
  live accumulation would have;
- FAILED records (initial send never succeeded, item never reached the
  background stage) are excluded by default to mirror live behavior;
- no Telegram, no OpenAI, no mutation of any news row;
- per-item isolation: one bad row logs a warning and the run continues;
- exact counts reported — never a fabricated coverage claim.

Run only via scripts/backfill_indicator_memory.py, rehearsed on a database
copy first (see the deployment runbook).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import NewsStatus
from app.log.logger import get_logger
from app.models.news import NewsEvent
from app.repositories.indicator_repository import IndicatorRepository
from app.services.indicators.engine import IndicatorMemoryEngine
from app.services.intelligence.engine import classify_news

logger = get_logger(__name__)


@dataclass
class BackfillReport:
    scanned: int = 0
    recorded_keyed: int = 0
    recorded_unkeyed: int = 0
    skipped_no_actual: int = 0
    already_recorded: int = 0
    failed_items: int = 0
    first_created_at: str | None = None
    last_created_at: str | None = None


async def backfill_indicator_memory(
    session: AsyncSession,
    *,
    limit: int | None = None,
    include_failed: bool = False,
) -> BackfillReport:
    engine = IndicatorMemoryEngine(session)
    repo = IndicatorRepository(session)

    statuses = [NewsStatus.PUBLISHED, NewsStatus.AI_FAILED]
    if include_failed:
        statuses.append(NewsStatus.FAILED)

    stmt = (
        select(NewsEvent)
        .where(NewsEvent.status.in_(statuses))
        .order_by(NewsEvent.created_at.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())

    report = BackfillReport(scanned=len(rows))
    for news in rows:
        # Snapshot plain values BEFORE record() commits: with an
        # expire-on-commit session, touching ORM attributes afterwards (even
        # in the except path) would itself raise under aiosqlite.
        news_id = news.id
        created_at = str(news.created_at)
        try:
            intelligence = classify_news(news.normalized_headline, news.source_url)
            if intelligence.actual is None:
                report.skipped_no_actual += 1
                continue
            if await repo.get_print_by_news_id(news_id) is not None:
                report.already_recorded += 1
                continue
            await engine.record(news, intelligence)
            recorded = await repo.get_print_by_news_id(news_id)
            if recorded is None:
                # record() returned without persisting (e.g. lost an
                # idempotency race) — count honestly, never guess.
                report.already_recorded += 1
                continue
            if recorded.series_id is not None:
                report.recorded_keyed += 1
            else:
                report.recorded_unkeyed += 1
            if report.first_created_at is None:
                report.first_created_at = created_at
            report.last_created_at = created_at
        except Exception as err:
            await session.rollback()
            report.failed_items += 1
            logger.warning(
                "Backfill item failed, continuing",
                news_id=news_id[:8],
                error_type=type(err).__name__,
            )
    logger.info(
        "Indicator memory backfill finished",
        scanned=report.scanned,
        keyed=report.recorded_keyed,
        unkeyed=report.recorded_unkeyed,
        skipped_no_actual=report.skipped_no_actual,
        already_recorded=report.already_recorded,
        failed_items=report.failed_items,
    )
    return report
