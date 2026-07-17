"""Six-hour digest scheduler.

Runs the digest exactly once per completed six-hour UTC window
(00–06, 06–12, 12–18, 18–24) inside the application process — the same
single-process asyncio pattern as ``RSSPoller``, so systemd's one uvicorn
instance guarantees one scheduler and no second publisher.

Recovery semantics (the max-recovery-age decision): on startup the
scheduler immediately runs the most recent completed window. The service
layer skips it if ``digest_state`` already records it, so a restart never
double-publishes — and after any outage, however long, at most that one
window is recovered. Older missed windows are permanently skipped: a
digest of stale news has negative reader value, and the pinned message
always reflects the latest completed period only.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib import import_module

from app.log.logger import get_logger
from app.services.digest.models import (
    DigestOutcome,
    DigestRunStatus,
    DigestWindow,
    next_boundary,
)

logger = get_logger(__name__)

# Updated by the scheduler after every run; read by the health endpoint.
_digest_status: str = "pending"
_last_digest_window: str = "never"
_last_digest_success_at: str = "never"


def get_digest_status() -> dict[str, str]:
    """Health-endpoint snapshot. Plain strings only; no secrets."""
    return {
        "digest_status": _digest_status,
        "last_digest_window": _last_digest_window,
        "last_digest_success_at": _last_digest_success_at,
        "next_digest_at": next_boundary(datetime.now(UTC)).isoformat(),
    }


class DigestScheduler:
    def __init__(
        self,
        runner: Callable[[DigestWindow], Awaitable[DigestOutcome]] | None = None,
    ) -> None:
        # None → resolved lazily to the service entry point on first run,
        # so importing the scheduler never drags in the whole service graph
        # (and tests can inject a fake runner).
        self._runner = runner
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info(
            "Digest scheduler started",
            next_boundary=next_boundary(datetime.now(UTC)).isoformat(),
        )

        # Startup recovery: process the most recent completed window now.
        # The service's digest_state check makes this idempotent, so a
        # restart shortly after a boundary publishes the missed digest once,
        # and a restart mid-window is a no-op.
        await self._run_once(DigestWindow.latest_completed())

        while self._running:
            boundary = next_boundary(datetime.now(UTC))
            delay = (boundary - datetime.now(UTC)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            if not self._running:
                break
            # No tight loop is possible here: next_boundary(now) is always
            # strictly later than now (latest_completed(now).end <= now, and
            # the boundary returned is that end + 6h), so each iteration
            # sleeps a genuinely future amount even when we wake exactly on
            # a boundary. Re-running an already-processed window is safe —
            # the service skips it via digest_state.
            await self._run_once(DigestWindow.latest_completed())

    async def close(self) -> None:
        self._running = False

    async def _run_once(self, window: DigestWindow) -> None:
        """Run one window through the service; never lets an error escape."""
        global _digest_status, _last_digest_window, _last_digest_success_at

        if self._runner is None:
            # Resolved lazily so importing the scheduler never drags in the
            # full service graph (and tests can inject a fake runner).
            module = import_module("app.services.digest.service")
            self._runner = module.run_digest_for_window

        logger.info(
            "Digest window due",
            window_start=window.start.isoformat(),
            window_end=window.end.isoformat(),
        )
        try:
            outcome = await self._runner(window)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _digest_status = "failed"
            logger.warning(
                "Digest run failed",
                window_start=window.start.isoformat(),
                error_type=type(e).__name__,
            )
            return

        _last_digest_window = window.start.isoformat()
        if outcome.status == DigestRunStatus.FAILED:
            _digest_status = "failed"
        else:
            _digest_status = "ok"
            if outcome.status == DigestRunStatus.COMPLETED:
                _last_digest_success_at = datetime.now(UTC).isoformat()
