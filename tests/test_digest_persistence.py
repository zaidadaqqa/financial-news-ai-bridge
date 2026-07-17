"""Digest state persistence: singleton-row semantics, race-safe creation,
attempt/success recording, and rollback behavior. The exactly-once
guarantee of the whole digest subsystem rests on this table."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models.digest import DIGEST_STATE_ID, DigestState
from app.repositories.digest_repository import DigestRepository, aware_utc
from tests.conftest import TestSessionLocal

WINDOW_START = datetime(2026, 7, 16, 6, 0, tzinfo=UTC)
SUCCESS_AT = datetime(2026, 7, 16, 12, 0, 5, tzinfo=UTC)


async def _row_count() -> int:
    async with TestSessionLocal() as session:
        result = await session.execute(select(func.count(DigestState.id)))
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent_single_row() -> None:
    async with TestSessionLocal() as session:
        repo = DigestRepository(session)
        first = await repo.get_or_create_state("@chan")
        second = await repo.get_or_create_state("@chan")
        assert first.id == second.id == DIGEST_STATE_ID
        await session.commit()
    assert await _row_count() == 1


@pytest.mark.asyncio
async def test_get_or_create_race_falls_back_to_reselect() -> None:
    # Winner commits first.
    async with TestSessionLocal() as session:
        repo = DigestRepository(session)
        await repo.get_or_create_state("@chan")
        await session.commit()

    # Loser: simulate the race by making the initial existence check miss,
    # so the code path INSERT → IntegrityError → rollback → re-select runs.
    async with TestSessionLocal() as session:
        repo = DigestRepository(session)
        original_get_state = repo.get_state
        calls = {"n": 0}

        async def racy_get_state() -> DigestState | None:
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return await original_get_state()

        repo.get_state = racy_get_state  # type: ignore[method-assign]
        state = await repo.get_or_create_state("@chan")
        assert state.id == DIGEST_STATE_ID
    assert await _row_count() == 1


@pytest.mark.asyncio
async def test_attempt_and_success_round_trip_fresh_session() -> None:
    async with TestSessionLocal() as session:
        repo = DigestRepository(session)
        await repo.get_or_create_state("@chan")
        await repo.record_attempt(WINDOW_START)
        await repo.record_success(
            window_start=WINDOW_START,
            message_id="777",
            fingerprint="ab" * 32,
            success_at=SUCCESS_AT,
        )
        await session.commit()

    async with TestSessionLocal() as session:
        state = await DigestRepository(session).get_state()
        assert state is not None
        assert aware_utc(state.last_attempted_window_start) == WINDOW_START
        assert aware_utc(state.last_completed_window_start) == WINDOW_START
        assert aware_utc(state.last_success_at) == SUCCESS_AT
        assert state.message_id == "777"
        assert state.content_fingerprint == "ab" * 32


@pytest.mark.asyncio
async def test_record_without_state_raises() -> None:
    async with TestSessionLocal() as session:
        repo = DigestRepository(session)
        with pytest.raises(RuntimeError):
            await repo.record_attempt(WINDOW_START)
        with pytest.raises(RuntimeError):
            await repo.record_success(
                window_start=WINDOW_START,
                message_id="1",
                fingerprint="f",
                success_at=SUCCESS_AT,
            )


@pytest.mark.asyncio
async def test_rollback_leaves_prior_state_intact() -> None:
    async with TestSessionLocal() as session:
        repo = DigestRepository(session)
        await repo.get_or_create_state("@chan")
        await session.commit()

    async with TestSessionLocal() as session:
        repo = DigestRepository(session)
        await repo.record_success(
            window_start=WINDOW_START,
            message_id="999",
            fingerprint="deadbeef",
            success_at=SUCCESS_AT,
        )
        await repo.rollback()

    async with TestSessionLocal() as session:
        state = await DigestRepository(session).get_state()
        assert state is not None
        assert state.message_id is None
        assert state.last_completed_window_start is None


def test_aware_utc_helper() -> None:
    assert aware_utc(None) is None
    naive = datetime(2026, 7, 16, 6, 0)
    assert aware_utc(naive) == WINDOW_START
    already = WINDOW_START
    assert aware_utc(already) == WINDOW_START
