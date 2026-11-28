from fastapi import APIRouter

from app.api.v1 import agents, executions, runs, runtimes, tasks, workflows

api_router = APIRouter()
api_router.include_router(agents.router)
api_router.include_router(workflows.router)
api_router.include_router(runs.router)
api_router.include_router(tasks.router)
api_router.include_router(executions.router)
api_router.include_router(runtimes.router)
