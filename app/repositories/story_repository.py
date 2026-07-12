from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.story import Story, StoryNews
from app.services.story.models import CANDIDATE_LIMIT, MAX_WINDOW_H


class StoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_candidates(self, now: datetime | None = None) -> list[Story]:
        """Recently active stories, newest activity first — bounded by the
        widest category window and CANDIDATE_LIMIT (indexed on
        last_updated_at; never a full-table load)."""
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(hours=MAX_WINDOW_H)
        result = await self.session.execute(
            select(Story)
            .where(Story.last_updated_at >= cutoff)
            .order_by(Story.last_updated_at.desc())
            .limit(CANDIDATE_LIMIT)
        )
        return list(result.scalars().all())

    async def get_story(self, story_id: str) -> Story | None:
        result = await self.session.execute(select(Story).filter_by(id=story_id))
        return result.scalars().first()

    async def get_link_by_news_id(self, news_id: str) -> StoryNews | None:
        result = await self.session.execute(
            select(StoryNews).filter_by(news_id=news_id)
        )
        return result.scalars().first()

    def add(self, obj: Story | StoryNews) -> None:
        self.session.add(obj)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
