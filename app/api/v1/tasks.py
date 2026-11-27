"""Task status endpoint (plan section 8)."""

from typing import Any

from fastapi import APIRouter

from app.api.v1._stub import not_implemented

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    raise not_implemented("Get task")
