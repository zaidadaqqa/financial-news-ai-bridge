from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "Financial News AI Bridge"}


@router.get("/metrics")
async def metrics() -> dict[str, str]:
    return {"message": "Metrics endpoint (to be implemented with Prometheus)"}
