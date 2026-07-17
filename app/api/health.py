from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import text

from app.database.connection import AsyncSessionLocal
from app.services.digest.scheduler import get_digest_status
from app.services.ingestion.rss_poller import get_last_poll_time

router = APIRouter()

_startup_time = datetime.now(UTC)


@router.get("/health")
async def health_check() -> dict[str, str]:
    uptime_seconds = int((datetime.now(UTC) - _startup_time).total_seconds())

    db_status = "ok"
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    last_poll = get_last_poll_time()
    last_poll_iso = last_poll.isoformat() if last_poll else "never"

    return {
        "status": "ok",
        "service": "Financial News AI Bridge",
        "uptime_seconds": str(uptime_seconds),
        "db_status": db_status,
        "last_rss_poll": last_poll_iso,
        # Additive digest fields — existing consumers only read the keys
        # above (verified: ops_report.py, deploy scripts, docs).
        **get_digest_status(),
    }
