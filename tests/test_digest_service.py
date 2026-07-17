"""Digest service + scheduler behavior: exactly-once per window, total
failure isolation from the news pipeline, quiet-window fallback, crash
recovery, and health status transitions. Telegram and the session
factory are faked; the news pipeline itself is never touched."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.services.digest.scheduler as scheduler_module
import app.services.digest.service as service_module
from app.constants.enums import NewsStatus
from app.exceptions.custom_exceptions import TelegramPublishError
from app.models.digest import DigestState
from app.models.news import NewsEvent
from app.repositories.digest_repository import DigestRepository, aware_utc
from app.services.digest.models import (
    DigestOutcome,
    DigestRunStatus,
    DigestWindow,
)
from app.services.digest.scheduler import DigestScheduler, get_digest_status
from app.services.digest.service import run_digest_for_window
from app.services.digest.telegram_ops import DigestPublishResult
from tests.conftest import TestSessionLocal

WINDOW = DigestWindow.from_start(datetime(2026, 7, 16, 6, 0, tzinfo=UTC))


class FakeOps:
    """Stands in for DigestTelegramOps — records calls, no network."""

    published: list[tuple[str, str | None]] = []
    fail_with: Exception | None = None
    next_message_id = "777"

    def __init__(self) -> None:
        pass

    async def publish_digest(
        self, text: str, existing_message_id: str | None
    ) -> DigestPublishResult:
        if FakeOps.fail_with is not None:
            raise FakeOps.fail_with
        FakeOps.published.append((text, existing_message_id))
        return DigestPublishResult(
            message_id=FakeOps.next_message_id,
            created=existing_message_id is None,
            pinned=True,
            unchanged=False,
        )

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _patch_service(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeOps.published = []
    FakeOps.fail_with = None
    FakeOps.next_message_id = "777"
    monkeypatch.setattr(service_module, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(service_module, "DigestTelegramOps", FakeOps)


def _news(guid: str, minutes: int = 10, importance: int = 4) -> NewsEvent:
    return NewsEvent(
        source_message_id=guid,
        source="rss",
        original_headline=f"original {guid}",
        normalized_headline=f"normalized {guid}",
        translated_headline="البنك المركزي يرفع أسعار الفائدة",
        category="central_bank",
        importance=importance,
        status=NewsStatus.PUBLISHED,
        hash=f"hash-{guid}",
        created_at=WINDOW.start + timedelta(minutes=minutes),
    )


async def _seed(*rows: NewsEvent) -> None:
    async with TestSessionLocal() as session:
        session.add_all(rows)
        await session.commit()


async def _state() -> DigestState | None:
    async with TestSessionLocal() as session:
        return await DigestRepository(session).get_state()


@pytest.mark.asyncio
async def test_completed_run_persists_state_and_fingerprint() -> None:
    await _seed(_news("g1"), _news("g2", minutes=20))
    outcome = await run_digest_for_window(WINDOW)
    assert outcome.status == DigestRunStatus.COMPLETED
    assert outcome.message_id == "777"
    assert outcome.entry_count >= 1

    state = await _state()
    assert state is not None
    assert state.message_id == "777"
    assert aware_utc(state.last_completed_window_start) == WINDOW.start
    text, existing = FakeOps.published[0]
    assert existing is None  # first ever run creates
    assert state.content_fingerprint == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.asyncio
async def test_exactly_once_second_run_skips_without_publishing() -> None:
    await _seed(_news("g1"))
    first = await run_digest_for_window(WINDOW)
    second = await run_digest_for_window(WINDOW)
    assert first.status == DigestRunStatus.COMPLETED
    assert second.status == DigestRunStatus.SKIPPED_ALREADY_PROCESSED
    assert second.message_id == "777"
    assert len(FakeOps.published) == 1


@pytest.mark.asyncio
async def test_subsequent_window_edits_same_message() -> None:
    await _seed(_news("g1"))
    await run_digest_for_window(WINDOW)

    next_window = DigestWindow.from_start(WINDOW.end)
    await _seed(_news("g9", minutes=int(6 * 60 + 30)))
    outcome = await run_digest_for_window(next_window)
    assert outcome.status == DigestRunStatus.COMPLETED
    assert FakeOps.published[1][1] == "777"  # edit path, same persisted id


@pytest.mark.asyncio
async def test_selection_failure_isolated_and_news_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(_news("g1"))

    async def boom(session: object, window: object) -> list:
        raise RuntimeError("selection exploded")

    monkeypatch.setattr(service_module, "select_digest_entries", boom)
    outcome = await run_digest_for_window(WINDOW)
    assert outcome.status == DigestRunStatus.FAILED
    assert outcome.detail == "RuntimeError"
    assert FakeOps.published == []

    async with TestSessionLocal() as session:
        rows = (await session.execute(select(NewsEvent))).scalars().all()
        assert all(row.status == NewsStatus.PUBLISHED for row in rows)


@pytest.mark.asyncio
async def test_telegram_failure_keeps_window_retryable() -> None:
    await _seed(_news("g1"))
    FakeOps.fail_with = TelegramPublishError("channel unavailable")
    outcome = await run_digest_for_window(WINDOW)
    assert outcome.status == DigestRunStatus.FAILED

    state = await _state()
    assert state is not None
    assert state.last_completed_window_start is None  # NOT advanced
    assert aware_utc(state.last_attempted_window_start) == WINDOW.start

    FakeOps.fail_with = None
    retry = await run_digest_for_window(WINDOW)
    assert retry.status == DigestRunStatus.COMPLETED


@pytest.mark.asyncio
async def test_quiet_window_publishes_honest_fallback_and_advances() -> None:
    outcome = await run_digest_for_window(WINDOW)
    assert outcome.status == DigestRunStatus.COMPLETED
    assert outcome.entry_count == 0
    text, _ = FakeOps.published[0]
    assert "لم تُسجَّل" in text
    state = await _state()
    assert state is not None
    assert aware_utc(state.last_completed_window_start) == WINDOW.start


@pytest.mark.asyncio
async def test_crash_after_publish_before_commit_recovers_same_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(_news("g1"))

    original = DigestRepository.record_success
    calls = {"n": 0}

    async def crashing_record_success(self: DigestRepository, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash before commit")
        await original(
            self,
            window_start=kwargs["window_start"],  # type: ignore[arg-type]
            message_id=kwargs["message_id"],  # type: ignore[arg-type]
            fingerprint=kwargs["fingerprint"],  # type: ignore[arg-type]
            success_at=kwargs["success_at"],  # type: ignore[arg-type]
        )

    monkeypatch.setattr(DigestRepository, "record_success", crashing_record_success)
    first = await run_digest_for_window(WINDOW)
    assert first.status == DigestRunStatus.FAILED
    assert len(FakeOps.published) == 1  # Telegram already delivered

    state = await _state()
    assert state is not None
    assert state.last_completed_window_start is None

    second = await run_digest_for_window(WINDOW)
    assert second.status == DigestRunStatus.COMPLETED
    assert len(FakeOps.published) == 2
    # The re-run has no persisted id yet (crash preceded the save), so it
    # publishes once more; from then on the persisted id is edited forever.
    third = await run_digest_for_window(WINDOW)
    assert third.status == DigestRunStatus.SKIPPED_ALREADY_PROCESSED
    assert len(FakeOps.published) == 2


@pytest.fixture()
def _fresh_scheduler_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_module, "_digest_status", "pending")
    monkeypatch.setattr(scheduler_module, "_last_digest_window", "never")
    monkeypatch.setattr(scheduler_module, "_last_digest_success_at", "never")


@pytest.mark.asyncio
async def test_scheduler_startup_recovery_runs_latest_completed_once(
    _fresh_scheduler_status: None,
) -> None:
    ran: list[DigestWindow] = []

    async def runner(window: DigestWindow) -> DigestOutcome:
        ran.append(window)
        return DigestOutcome(status=DigestRunStatus.COMPLETED, window=window)

    scheduler = DigestScheduler(runner=runner)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)
    await scheduler.close()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ran == [DigestWindow.latest_completed()]
    status = get_digest_status()
    assert status["digest_status"] == "ok"
    assert status["last_digest_window"] == ran[0].start.isoformat()


@pytest.mark.asyncio
async def test_scheduler_survives_raising_runner(
    _fresh_scheduler_status: None,
) -> None:
    async def runner(window: DigestWindow) -> DigestOutcome:
        raise RuntimeError("boom")

    scheduler = DigestScheduler(runner=runner)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)
    assert not task.done()  # loop alive despite the failure
    assert get_digest_status()["digest_status"] == "failed"
    await scheduler.close()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_scheduler_status_transitions(
    _fresh_scheduler_status: None,
) -> None:
    assert get_digest_status()["digest_status"] == "pending"

    async def ok_runner(window: DigestWindow) -> DigestOutcome:
        return DigestOutcome(status=DigestRunStatus.COMPLETED, window=window)

    async def failed_runner(window: DigestWindow) -> DigestOutcome:
        return DigestOutcome(status=DigestRunStatus.FAILED, window=window)

    scheduler = DigestScheduler(runner=ok_runner)
    await scheduler._run_once(WINDOW)
    status = get_digest_status()
    assert status["digest_status"] == "ok"
    assert status["last_digest_success_at"] != "never"

    scheduler._runner = failed_runner
    await scheduler._run_once(WINDOW)
    assert get_digest_status()["digest_status"] == "failed"

    # next_digest_at is always a parseable future boundary.
    next_at = datetime.fromisoformat(get_digest_status()["next_digest_at"])
    assert next_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_skip_counts_as_ok_but_not_success_time(
    _fresh_scheduler_status: None,
) -> None:
    async def skip_runner(window: DigestWindow) -> DigestOutcome:
        return DigestOutcome(
            status=DigestRunStatus.SKIPPED_ALREADY_PROCESSED, window=window
        )

    scheduler = DigestScheduler(runner=skip_runner)
    await scheduler._run_once(WINDOW)
    status = get_digest_status()
    assert status["digest_status"] == "ok"
    assert status["last_digest_success_at"] == "never"


def test_importing_main_starts_nothing() -> None:
    import app.main as main_module

    assert main_module._rss_poller is None
    assert main_module._digest_scheduler is None


@pytest.mark.asyncio
async def test_health_reports_digest_fields_as_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.health as health_module

    monkeypatch.setattr(health_module, "AsyncSessionLocal", TestSessionLocal)
    body = await health_module.health_check()
    for key in (
        "digest_status",
        "last_digest_window",
        "last_digest_success_at",
        "next_digest_at",
    ):
        assert key in body
        assert isinstance(body[key], str)
    assert body["db_status"] == "ok"
