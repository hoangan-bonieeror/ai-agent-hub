"""Agent endpoints (plan section 8). CRUD goes through the agent registry."""

from typing import Any

from fastapi import APIRouter

from app.api.v1._stub import not_implemented

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", status_code=201)
async def create_agent(payload: dict[str, Any]) -> dict[str, Any]:
    raise not_implemented("Create agent")


@router.get("")
async def list_agents() -> list[dict[str, Any]]:
    raise not_implemented("List agents")


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    raise not_implemented("Get agent")


@router.patch("/{agent_id}")
async def update_agent(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise not_implemented("Update agent")


@router.post("/{agent_id}/run")
async def run_agent(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise not_implemented("Run agent")
