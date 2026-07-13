#!/usr/bin/env python3
"""Bounded Indicator Memory backfill CLI (Phase 4B).

Replays existing validated news rows through the exact live accumulation
path. Idempotent — safe to re-run; a second run records nothing new.

ALWAYS rehearse on a COPY of the production database first:

    .venv/bin/python scripts/backfill_indicator_memory.py --db /path/to/copy.db

Production run (after rehearsal + fresh backup, per the runbook):

    .venv/bin/python scripts/backfill_indicator_memory.py \
        --db data/news.db --confirm-production

The script writes ONLY to indicator_series / indicator_prints. It never
touches news rows, never calls Telegram or OpenAI.
"""

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.indicator import IndicatorPrint, IndicatorSeries  # noqa: E402
from app.services.indicators.backfill import (  # noqa: E402
    backfill_indicator_memory,
)


async def _quality_summary(session: AsyncSession) -> list[str]:
    lines: list[str] = ["", "=== INDICATOR MEMORY QUALITY SUMMARY ==="]
    series = (await session.execute(select(IndicatorSeries))).scalars().all()
    prints = (await session.execute(select(IndicatorPrint))).scalars().all()
    unkeyed = [p for p in prints if p.series_id is None]
    lines.append(f"series: {len(series)}")
    lines.append(
        f"prints: {len(prints)} (keyed {len(prints) - len(unkeyed)}, "
        f"unkeyed {len(unkeyed)})"
    )
    reasons: dict[str, int] = {}
    for p in unkeyed:
        reasons[p.unkeyed_reason or "?"] = reasons.get(p.unkeyed_reason or "?", 0) + 1
    lines.append(f"unkeyed reasons: {reasons}")
    lines.append("per-series print counts:")
    for s in sorted(series, key=lambda s: s.print_count, reverse=True):
        lines.append(f"  {s.print_count:>3}  {s.canonical_key}")
    return lines


async def _run(db_path: str, limit: int | None, include_failed: bool) -> int:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    # expire_on_commit=False matches the app's own sessionmaker — without it
    # every commit inside record() expires the loaded rows and the next
    # attribute access explodes under aiosqlite (found in rehearsal).
    sessionmaker = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessionmaker() as session:
        # Fail fast if the schema is missing (wrong file / pre-4A copy).
        await session.execute(text("SELECT 1 FROM indicator_prints LIMIT 1"))
        report = await backfill_indicator_memory(
            session, limit=limit, include_failed=include_failed
        )
        print("=== BACKFILL REPORT ===")
        print(f"scanned:            {report.scanned}")
        print(f"recorded keyed:     {report.recorded_keyed}")
        print(f"recorded unkeyed:   {report.recorded_unkeyed}")
        print(f"skipped (no data):  {report.skipped_no_actual}")
        print(f"already recorded:   {report.already_recorded}")
        print(f"failed items:       {report.failed_items}")
        print(f"first recorded row: {report.first_created_at}")
        print(f"last recorded row:  {report.last_created_at}")
        for line in await _quality_summary(session):
            print(line)
    await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the SQLite database")
    parser.add_argument("--limit", type=int, default=None, help="Row bound")
    parser.add_argument(
        "--include-failed",
        action="store_true",
        help="Also replay FAILED rows (excluded by default — they never "
        "reached the live background stage)",
    )
    parser.add_argument(
        "--confirm-production",
        action="store_true",
        help="Required when --db points at data/news.db",
    )
    args = parser.parse_args()

    if args.db.endswith("data/news.db") and not args.confirm_production:
        print(
            "Refusing to touch what looks like the live database without "
            "--confirm-production. Rehearse on a copy first."
        )
        return 2
    return asyncio.run(_run(args.db, args.limit, args.include_failed))


if __name__ == "__main__":
    raise SystemExit(main())
