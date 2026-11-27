from fastapi import APIRouter, Response, status

from app.core.database import check_database
from app.core.redis import check_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(response: Response) -> dict[str, str]:
    """Readiness: PostgreSQL and Redis are reachable."""
    db_ok, redis_ok = await check_database(), await check_redis()
    if not (db_ok and redis_ok):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if db_ok and redis_ok else "degraded",
        "postgres": "ok" if db_ok else "unavailable",
        "redis": "ok" if redis_ok else "unavailable",
    }
