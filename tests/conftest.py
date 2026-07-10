import os
import tempfile
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base

_test_db_file = os.path.join(tempfile.gettempdir(), "financial_news_test.db")
TEST_DATABASE_URL = f"sqlite+aiosqlite:///{_test_db_file}"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False, future=True)

TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
