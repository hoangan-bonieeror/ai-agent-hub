from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import get_engine
from app.core.redis import close_redis
from app.orchestrator.runner import get_run_manager


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await get_run_manager().shutdown()  # background runs are recorded as cancelled
    await close_redis()
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
