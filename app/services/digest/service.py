"""One digest window execution — the digest subsystem's isolation boundary.

``run_digest_for_window`` is the only entry point the scheduler calls. It
owns its own database session, enforces exactly-once per window through
``digest_state``, and catches every exception: a digest failure is logged
and reported in the returned outcome, but can never propagate into the
scheduler loop, touch a news row's status, or interfere with the
individual-news publishing pipeline.

Idempotency model: ``digest_state.last_completed_window_start`` is the
authority. Running the same window twice returns a skip; a crash after
the Telegram edit but before the DB commit re-runs safely because the
retry edits the same persisted message with identical content (Telegram's
"message is not modified" is treated as success by the publisher).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.config.settings import settings
from app.database.connection import AsyncSessionLocal
from app.log.logger import get_logger
from app.repositories.digest_repository import DigestRepository, aware_utc
from app.services.digest.formatter import render_digest, render_quiet_digest
from app.services.digest.models import DigestOutcome, DigestRunStatus, DigestWindow
from app.services.digest.selection import select_digest_entries
from app.services.digest.telegram_ops import DigestTelegramOps

logger = get_logger(__name__)


async def run_digest_for_window(window: DigestWindow) -> DigestOutcome:
    try:
        return await _run(window)
    except Exception as e:
        logger.warning(
            "Digest generation failed",
            window_start=window.start.isoformat(),
            error=type(e).__name__,
        )
        return DigestOutcome(
            status=DigestRunStatus.FAILED, window=window, detail=type(e).__name__
        )


async def _run(window: DigestWindow) -> DigestOutcome:
    started = datetime.now(UTC)
    logger.info(
        "Digest window started",
        window_start=window.start.isoformat(),
        window_end=window.end.isoformat(),
    )

    async with AsyncSessionLocal() as session:
        repo = DigestRepository(session)
        state = await repo.get_or_create_state(settings.TELEGRAM_CHAT_ID)

        if aware_utc(state.last_completed_window_start) == window.start:
            logger.info(
                "Digest window already processed, skipping",
                window_start=window.start.isoformat(),
            )
            return DigestOutcome(
                status=DigestRunStatus.SKIPPED_ALREADY_PROCESSED,
                window=window,
                message_id=state.message_id,
            )

        await repo.record_attempt(window.start)
        await session.commit()

        entries = await select_digest_entries(session, window)
        logger.info(
            "Digest candidates selected",
            window_start=window.start.isoformat(),
            selected=len(entries),
        )

        now = datetime.now(UTC)
        if entries:
            text = render_digest(entries, window, now)
        else:
            text = render_quiet_digest(window, now)
            logger.info(
                "Digest fallback used",
                window_start=window.start.isoformat(),
                reason="no_eligible_news",
            )
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()

        existing_message_id = state.message_id
        ops = DigestTelegramOps()
        try:
            result = await ops.publish_digest(text, existing_message_id)
        finally:
            await ops.close()

        await repo.record_success(
            window_start=window.start,
            message_id=result.message_id,
            fingerprint=fingerprint,
            success_at=datetime.now(UTC),
        )
        await session.commit()

    duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    logger.info(
        "Digest window completed",
        window_start=window.start.isoformat(),
        message_id=result.message_id,
        created=result.created,
        pinned=result.pinned,
        entries=len(entries),
        duration_ms=duration_ms,
    )
    return DigestOutcome(
        status=DigestRunStatus.COMPLETED,
        window=window,
        message_id=result.message_id,
        entry_count=len(entries),
    )
