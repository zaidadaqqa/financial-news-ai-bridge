from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import NewsStatus
from app.models.news import NewsEvent
from app.repositories.base import BaseRepository


class NewsRepository(BaseRepository[NewsEvent]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, NewsEvent)

    async def get_by_hash(self, hash_val: str) -> NewsEvent | None:
        result = await self.session.execute(select(NewsEvent).filter_by(hash=hash_val))
        return result.scalars().first()

    async def update_status(
        self, news_id: str, new_status: NewsStatus
    ) -> NewsEvent | None:
        news = await self.get_by_id(news_id)
        if news:
            news.status = new_status
            await self.session.flush()
        return news
