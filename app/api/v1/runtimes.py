"""Which agent runtimes this server can execute: /api/v1/runtimes"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.orchestrator.dependencies import get_runtime_registry
from app.runtimes import RuntimeRegistry

router = APIRouter(prefix="/runtimes", tags=["runtimes"])


@router.get("")
async def list_runtimes(
    runtimes: Annotated[RuntimeRegistry, Depends(get_runtime_registry)],
) -> dict[str, Any]:
    """Runtime names an agent's ``runtime`` field can use on this server."""
    return {"items": runtimes.names()}
