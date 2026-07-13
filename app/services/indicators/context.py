"""Macro Context (Phase 4B) — the deterministic read path of Indicator Memory.

Turns the silently-accumulated print history (Phase 4A) into a small set of
authoritative historical facts about the CURRENT print: forecast streaks,
value streaks, recorded extremes, revision status, and — only when the
headline itself carries no Previous figure — the last print this desk
recorded. Everything is computed with Decimal from the canonical
`*_dec` columns inside one canonical series, so no comparison can ever cross
countries, events, variants, unit classes, or seasonal-adjustment bases
(series identity already separates them — see parser.py).

Honesty gates (PHASE_4_ARCHITECTURE.md §9, permanent):
- no streak claim unless the series holds >= MIN_PRINTS_FOR_STREAK prints,
- no extreme claim unless >= MIN_PRINTS_FOR_EXTREME comparable prints,
- every extreme is "within our records" — never a longer-history claim,
- a series containing revisions suppresses streaks/extremes entirely
  (a revised period would be double-counted; wrong history is worse than
  missing history),
- insufficient history returns None and the pipeline behaves exactly as
  Phase 4A/Phase 3 — silence is always better than invention.

This module never writes. AI may phrase these facts; it never computes them.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.log.logger import get_logger
from app.models.indicator import IndicatorPrint
from app.models.news import NewsEvent
from app.repositories.indicator_repository import IndicatorRepository
from app.services.intelligence.models import NewsIntelligenceResult

logger = get_logger(__name__)

MIN_PRINTS_FOR_STREAK = 3
MIN_PRINTS_FOR_EXTREME = 6
MAX_FACTS_IN_CONTEXT = 3


@dataclass(frozen=True)
class MacroContext:
    """Deterministic historical facts about the current print. A zero/None
    field means "no claim" — a fact either passed its evidence gate or it
    does not exist. All wording built from this must stay within-our-records
    honest."""

    history_count: int
    history_since: datetime
    # >= 2 when the current print is the Nth consecutive above/below
    # forecast (counted in prints, including the current one); 0 = no claim.
    forecast_streak: int
    forecast_streak_direction: str | None  # "above" | "below"
    # >= 2 when the value moved the same direction for N consecutive
    # recorded prints (counted in moves, each print vs its predecessor).
    value_streak: int
    value_streak_direction: str | None  # "risen" | "fallen"
    extreme: str | None  # "highest" | "lowest" strictly within our records
    is_revision: bool
    # Only populated when the source headline itself carried no Previous
    # figure — otherwise this desk's prior print adds nothing the reader's
    # headline doesn't already show.
    prior_actual_raw: str | None
    prior_print_at: datetime | None

    @property
    def has_facts(self) -> bool:
        return bool(
            self.forecast_streak
            or self.value_streak
            or self.extreme
            or self.is_revision
            or self.prior_actual_raw
        )


def _actual(print_row: IndicatorPrint) -> Decimal | None:
    return Decimal(print_row.actual_dec) if print_row.actual_dec else None


class MacroContextReader:
    """Read-only. Failure of any kind must degrade to 'no context' — the
    orchestrator wraps the single call in its own try/except."""

    def __init__(self, session: AsyncSession):
        self.repo = IndicatorRepository(session)

    async def read(
        self, news: NewsEvent, intelligence: NewsIntelligenceResult
    ) -> MacroContext | None:
        current = await self.repo.get_print_by_news_id(news.id)
        if current is None or current.series_id is None:
            return None  # not a structured print, or honestly unkeyed

        series = await self.repo.get_series_by_id(current.series_id)
        if series is None:
            return None

        prints = await self.repo.list_series_prints(current.series_id)
        position = next((i for i, p in enumerate(prints) if p.id == current.id), None)
        if position is None:
            return None
        history = prints[: position + 1]  # chronological, ends at current
        total = len(history)
        if total < 2:
            return None  # a series' first print has no history to speak of

        is_revision = current.revision_of is not None
        if series.revision_count > 0:
            # A revised period appears twice in this series' print list —
            # any streak/extreme computed over it would double-count.
            # Conservative: state only the revision relationship itself.
            if not is_revision:
                return None
            context = MacroContext(
                history_count=total,
                history_since=history[0].print_at,
                forecast_streak=0,
                forecast_streak_direction=None,
                value_streak=0,
                value_streak_direction=None,
                extreme=None,
                is_revision=True,
                prior_actual_raw=None,
                prior_print_at=None,
            )
            logger.debug("Macro context: revision-only", series=series.canonical_key)
            return context

        current_value = _actual(current)

        # Forecast streak — walks the surprise directions the frozen engine
        # already validated at record time.
        forecast_streak = 0
        forecast_direction: str | None = None
        if current.surprise_direction in ("HIGHER", "LOWER"):
            run = 0
            for print_row in reversed(history):
                if print_row.surprise_direction == current.surprise_direction:
                    run += 1
                else:
                    break
            if run >= 2 and total >= MIN_PRINTS_FOR_STREAK:
                forecast_streak = run
                forecast_direction = (
                    "above" if current.surprise_direction == "HIGHER" else "below"
                )

        # Value streak — consecutive same-direction moves ending at the
        # current print. An equal or unparseable value breaks the walk.
        value_streak = 0
        value_direction: str | None = None
        if current_value is not None:
            moves = 0
            direction: int | None = None
            later_value = current_value
            for print_row in reversed(history[:-1]):
                value = _actual(print_row)
                if value is None:
                    break
                if later_value > value:
                    step = 1
                elif later_value < value:
                    step = -1
                else:
                    break
                if direction is None:
                    direction = step
                elif step != direction:
                    break
                moves += 1
                later_value = value
            if moves >= 2 and total >= MIN_PRINTS_FOR_STREAK:
                value_streak = moves
                value_direction = "risen" if direction == 1 else "fallen"

        # Recorded extreme — strict (a tie with any earlier print is not an
        # extreme), and only once enough comparable prints exist.
        extreme: str | None = None
        earlier_values = [
            v for v in (_actual(p) for p in history[:-1]) if v is not None
        ]
        if (
            current_value is not None
            and earlier_values
            and len(earlier_values) + 1 >= MIN_PRINTS_FOR_EXTREME
        ):
            if all(current_value > v for v in earlier_values):
                extreme = "highest"
            elif all(current_value < v for v in earlier_values):
                extreme = "lowest"

        # Prior recorded print — useful only when the headline itself gave
        # the reader no Previous figure.
        prior_actual_raw: str | None = None
        prior_print_at: datetime | None = None
        if intelligence.previous is None:
            prior = history[-2]
            if prior.actual_raw:
                prior_actual_raw = prior.actual_raw
                prior_print_at = prior.print_at

        context = MacroContext(
            history_count=total,
            history_since=history[0].print_at,
            forecast_streak=forecast_streak,
            forecast_streak_direction=forecast_direction,
            value_streak=value_streak,
            value_streak_direction=value_direction,
            extreme=extreme,
            is_revision=is_revision,
            prior_actual_raw=prior_actual_raw,
            prior_print_at=prior_print_at,
        )
        if not context.has_facts:
            return None
        logger.debug(
            "Macro context computed",
            series=series.canonical_key,
            prints=total,
            forecast_streak=forecast_streak,
            value_streak=value_streak,
            extreme=extreme,
        )
        return context
