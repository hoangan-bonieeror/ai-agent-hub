"""Workflow endpoints (plan section 8)."""

from typing import Any

from fastapi import APIRouter

from app.api.v1._stub import not_implemented

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("", status_code=201)
async def create_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    raise not_implemented("Create workflow")


@router.post("/{workflow_id}/run", status_code=202)
async def run_workflow(workflow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise not_implemented("Run workflow")
