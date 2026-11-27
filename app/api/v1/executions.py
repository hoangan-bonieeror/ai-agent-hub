"""Execution and log endpoint (plan section 8)."""

from typing import Any

from fastapi import APIRouter

from app.api.v1._stub import not_implemented

router = APIRouter(prefix="/executions", tags=["executions"])


@router.get("/{execution_id}")
async def get_execution(execution_id: str) -> dict[str, Any]:
    raise not_implemented("Get execution")
