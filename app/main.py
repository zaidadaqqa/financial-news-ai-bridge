import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.health import router as health_router
from app.config.settings import settings
from app.database.connection import engine
from app.log.logger import get_logger, setup_logging
from app.models.base import Base
from app.services.discord.bot import BridgeDiscordClient

setup_logging(settings.LOG_LEVEL)
logger = get_logger(__name__)


# Note: In a production setting with Alembic, we don't use create_all().
# We're including it here for development/demonstration if migrations aren't run.
async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Startup
    logger.info("Starting Financial News AI Bridge...")
    await init_db()

    # Start Discord Bot as a background task
    token = settings.DISCORD_BOT_TOKEN.get_secret_value()
    if token and token != "YOUR_DISCORD_BOT_TOKEN_HERE":
        discord_client = BridgeDiscordClient()
        asyncio.create_task(discord_client.start(token))
    else:
        logger.warning("DISCORD_BOT_TOKEN not configured. Discord bot will not start.")

    yield

    # Shutdown
    logger.info("Shutting down gracefully...")


app = FastAPI(title="Financial News AI Bridge", lifespan=lifespan)

app.include_router(health_router)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
