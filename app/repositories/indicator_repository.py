from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.indicator import IndicatorPrint, IndicatorSeries


class IndicatorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_series_by_key(self, canonical_key: str) -> IndicatorSeries | None:
        result = await self.session.execute(
            select(IndicatorSeries).filter_by(canonical_key=canonical_key)
        )
        return result.scalars().first()

    async def get_print_by_news_id(self, news_id: str) -> IndicatorPrint | None:
        result = await self.session.execute(
            select(IndicatorPrint).filter_by(news_id=news_id)
        )
        return result.scalars().first()

    async def latest_print(self, series_id: str) -> IndicatorPrint | None:
        result = await self.session.execute(
            select(IndicatorPrint)
            .filter_by(series_id=series_id)
            .order_by(IndicatorPrint.print_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    def add(self, obj: IndicatorSeries | IndicatorPrint) -> None:
        self.session.add(obj)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    @staticmethod
    def quality_score(series: IndicatorSeries) -> int:
        """Engineering-only quality metric (0-100), derived from stored
        counters — never persisted, never user-visible. Penalizes unit
        mismatches and failed comparisons; rewards accumulated history.
        Used to identify weak series BEFORE any future phase lets them
        influence production (Phase 4B gate input)."""
        if series.print_count == 0:
            return 0
        clean = (
            series.print_count
            - series.unit_mismatch_count
            - series.unknown_surprise_count
        )
        base = max(0, int(100 * clean / series.print_count))
        history_bonus = min(10, series.print_count)  # confidence grows with data
        return max(0, min(100, base + history_bonus - 10))
