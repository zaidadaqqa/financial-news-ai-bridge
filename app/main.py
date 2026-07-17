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
from app.services.digest.scheduler import DigestScheduler
from app.services.ingestion.rss_poller import RSSPoller

setup_logging(settings.LOG_LEVEL)
logger = get_logger(__name__)

_rss_poller: RSSPoller | None = None
_digest_scheduler: DigestScheduler | None = None


def _run_migrations() -> None:
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
    global _rss_poller, _digest_scheduler

    logger.info("Starting Financial News AI Bridge", env=settings.APP_ENV)

    try:
        _run_migrations()
    except Exception as e:
        logger.error("Migration failed, aborting startup", error=type(e).__name__)
        raise

    _rss_poller = RSSPoller()
    rss_task = asyncio.create_task(_rss_poller.start())
    logger.info("RSS poller started in background")

    _digest_scheduler = DigestScheduler()
    digest_task = asyncio.create_task(_digest_scheduler.start())
    logger.info("Digest scheduler started in background")

    yield

    logger.info("Shutting down gracefully...")
    if _rss_poller:
        await _rss_poller.close()
    if _digest_scheduler:
        await _digest_scheduler.close()
    for task in (rss_task, digest_task):
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
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
