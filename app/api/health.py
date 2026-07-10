from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter()

_startup_time = datetime.now(UTC)


@router.get("/health")
async def health_check() -> dict[str, str]:
    uptime_seconds = int((datetime.now(UTC) - _startup_time).total_seconds())
    return {
        "status": "ok",
        "service": "Financial News AI Bridge",
        "uptime_seconds": str(uptime_seconds),
    }
