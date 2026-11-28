"""The agent network: /api/v1/network

Feeds the dashboard's Network page. Every agent is a node, and two agents are
linked when they took part in the same workflow runs. A link's ``runs`` is the
number of runs they shared (its strength), and ``handoffs`` counts the times
one agent's step waited for the other's, so the page can show which way work
flows.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.models import Agent, Memory, Task, WorkflowRun

router = APIRouter(tags=["network"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: Most recent runs looked at, so the page stays fast with a long history.
MAX_RUNS = 500


@router.get("/network")
async def agent_network(
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365, description="Look back this many days")] = 30,
) -> dict[str, Any]:
    """Agents as nodes, linked by the runs they shared in the last ``days`` days."""
    since = datetime.now(UTC) - timedelta(days=days)
    run_rows = (
        await session.execute(
            select(WorkflowRun.id)
            .where(WorkflowRun.created_at >= since)
            .order_by(WorkflowRun.created_at.desc())
            .limit(MAX_RUNS)
        )
    ).all()
    run_ids = [r.id for r in run_rows]

    tasks: list[Any] = []
    if run_ids:
        tasks = list(
            (
                await session.execute(
                    select(
                        Task.run_id,
                        Task.name,
                        Task.assigned_agent_id,
                        Task.depends_on,
                        Task.status,
                        Task.updated_at,
                    ).where(Task.run_id.in_(run_ids), Task.parent_task_id.is_(None))
                )
            ).all()
        )

    agents = (
        (
            await session.execute(
                select(Agent).where(Agent.status != "archived").order_by(Agent.slug)
            )
        )
        .scalars()
        .all()
    )
    memory_counts = dict(
        (
            await session.execute(
                select(Memory.owner, func.count(Memory.id))
                .where(Memory.scope == "agent")
                .group_by(Memory.owner)
            )
        ).all()
    )

    slug_of: dict[uuid.UUID, str] = {a.id: a.slug for a in agents}
    stats: dict[str, dict[str, Any]] = {a.slug: _empty_stats() for a in agents}
    agents_in_run: dict[uuid.UUID, set[str]] = defaultdict(set)
    step_agent: dict[tuple[uuid.UUID, str], str] = {}

    for t in tasks:
        slug = slug_of.get(t.assigned_agent_id) if t.assigned_agent_id else None
        if slug is None:
            continue
        agents_in_run[t.run_id].add(slug)
        if t.name:
            step_agent[(t.run_id, t.name)] = slug
        s = stats[slug]
        s["runs"].add(t.run_id)
        s["tasks"] += 1
        if t.status == "failed":
            s["failed"] += 1
        if t.status in ("running", "queued"):
            s["running"] += 1
        newer = s["last_at"] is None or t.updated_at > s["last_at"]
        if t.status in ("succeeded", "failed", "running") and newer:
            s["last_at"] = t.updated_at
            s["last_status"] = t.status

    shared: Counter[tuple[str, str]] = Counter()
    for members in agents_in_run.values():
        ordered = sorted(members)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                shared[(a, b)] += 1

    handoffs: Counter[tuple[str, str]] = Counter()
    for t in tasks:
        target = slug_of.get(t.assigned_agent_id) if t.assigned_agent_id else None
        if target is None:
            continue
        for dep in t.depends_on or []:
            source = step_agent.get((t.run_id, dep))
            if source and source != target:
                handoffs[(source, target)] += 1

    links = []
    for (a, b), runs in shared.most_common():
        links.append(
            {
                "source": a,
                "target": b,
                "runs": runs,
                "handoffs": {a: handoffs[(a, b)], b: handoffs[(b, a)]},
            }
        )

    nodes = [_node(a, stats[a.slug], memory_counts.get(a.slug, 0)) for a in agents]
    return {"days": days, "runs": len(run_ids), "nodes": nodes, "links": links}


def _empty_stats() -> dict[str, Any]:
    return {
        "runs": set(),
        "tasks": 0,
        "failed": 0,
        "running": 0,
        "last_at": None,
        "last_status": None,
    }


def _node(a: Agent, s: dict[str, Any], memories: int) -> dict[str, Any]:
    return {
        "id": a.slug,
        "name": a.name,
        "status": a.status,
        "runtime": a.runtime,
        "model": a.model,
        "tools": len((a.config or {}).get("tools") or []),
        "memories": memories,
        "runs": len(s["runs"]),
        "tasks": s["tasks"],
        "failed": s["failed"],
        "running": s["running"],
        "last_status": s["last_status"],
        "last_active_at": s["last_at"].isoformat() if s["last_at"] else None,
    }
