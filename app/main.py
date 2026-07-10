import asyncio
import os
import signal
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.health import router as health_router
from app.config.settings import settings
from app.log.logger import get_logger, setup_logging
from app.services.discord.bot import BridgeDiscordClient

setup_logging(settings.LOG_LEVEL)
logger = get_logger(__name__)

_discord_client: BridgeDiscordClient | None = None


def _run_migrations() -> None:
    """Run Alembic migrations synchronously using a plain sqlite3 connection."""
    import subprocess
    import sys

    os.makedirs("data", exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Migration failed", stderr=result.stderr[:300])
        raise RuntimeError(f"Migration failed: {result.stderr[:200]}")
    logger.info("Database migrations applied successfully")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    global _discord_client

    logger.info("Starting Financial News AI Bridge", env=settings.APP_ENV)

    try:
        _run_migrations()
    except Exception as e:
        logger.error("Migration failed, aborting startup", error=type(e).__name__)
        raise

    token = settings.DISCORD_BOT_TOKEN.get_secret_value()
    if token and not token.startswith("replace-") and token != "test-discord-token":
        _discord_client = BridgeDiscordClient()
        discord_task = asyncio.create_task(_discord_client.start(token))
        logger.info("Discord bot starting in background")
    else:
        logger.warning("DISCORD_BOT_TOKEN not configured — Discord bot will not start")
        discord_task = None

    yield

    logger.info("Shutting down gracefully...")
    if _discord_client and not _discord_client.is_closed():
        await _discord_client.close()
    if discord_task and not discord_task.done():
        discord_task.cancel()
        try:
            await asyncio.wait_for(discord_task, timeout=5.0)
        except (asyncio.CancelledError, TimeoutError):
            pass
    logger.info("Shutdown complete")


app = FastAPI(title="Financial News AI Bridge", lifespan=lifespan)

app.include_router(health_router)


def _handle_sigterm(signum: int, frame: object) -> None:
    logger.info("Received SIGTERM, initiating graceful shutdown")
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )
