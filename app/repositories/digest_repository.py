"""Repository for the digest singleton state row.

Commit convention (matches StoryRepository/BaseRepository): methods
mutate and ``flush`` inside the current transaction; the CALLER commits
via ``commit()`` (or rolls back). The digest service owns its session
and transaction boundaries.

SQLite datetime note: aware-UTC datetimes are stored, but reads come
back naive (UTC wall-clock). Comparisons against window boundaries must
re-attach UTC — ``aware_utc()`` is provided for exactly that.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.digest import DIGEST_STATE_ID, DigestState


def aware_utc(value: datetime | None) -> datetime | None:
    """Re-attach UTC to a naive datetime read back from SQLite."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class DigestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_state(self) -> DigestState | None:
        result = await self.session.execute(
            select(DigestState).filter_by(id=DIGEST_STATE_ID)
        )
        return result.scalars().first()

    async def get_or_create_state(self, chat_id: str) -> DigestState:
        """Fetch the singleton row, creating it on first ever run.

        Race/restart safe: a concurrent insert loses on the fixed primary
        key, rolls back, and re-selects the winner's row.
        """
        state = await self.get_state()
        if state is not None:
            return state

        state = DigestState(id=DIGEST_STATE_ID, chat_id=chat_id)
        self.session.add(state)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_state()
            if existing is None:  # pragma: no cover - rollback always exposes it
                raise
            return existing
        return state

    async def record_attempt(self, window_start: datetime) -> None:
        state = await self.get_state()
        if state is None:
            raise RuntimeError("digest_state row missing; call get_or_create_state")
        state.last_attempted_window_start = window_start
        await self.session.flush()

    async def record_success(
        self,
        window_start: datetime,
        message_id: str,
        fingerprint: str,
        success_at: datetime,
    ) -> None:
        state = await self.get_state()
        if state is None:
            raise RuntimeError("digest_state row missing; call get_or_create_state")
        state.last_completed_window_start = window_start
        state.message_id = message_id
        state.content_fingerprint = fingerprint
        state.last_success_at = success_at
        await self.session.flush()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
