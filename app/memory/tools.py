"""The memory tools an agent gets when its ``tools`` list contains ``memory``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents import FunctionTool, function_tool

from app.memory.store import MAX_CONTENT, MemoryStore

MEMORY_GRANT = "memory"


def memory_tools(store: MemoryStore, agent_id: str) -> list[FunctionTool]:
    """``memory_search`` and ``memory_save``, bound to one agent: it reads its
    own and shared memories and cannot see other agents' private ones."""

    @function_tool
    async def memory_search(query: str, limit: int = 5) -> str:
        """Search what you (and other agents, in shared memory) saved in earlier runs.

        Args:
            query: Words to look for.
            limit: Most results to return (1-10).
        """
        found = await store.search(query, agent=agent_id, limit=max(1, min(limit, 10)))
        if not found:
            return "No memories match."
        return "\n".join(
            f"- [{m.scope}{', ' + ', '.join(m.tags) if m.tags else ''}] "
            f"({m.updated_at:%Y-%m-%d}) {m.content}"
            for m in found
        )

    @function_tool
    async def memory_save(
        content: str,
        shared: bool = False,
        tags: list[str] | None = None,
        expires_in_days: int | None = None,
    ) -> str:
        """Save a fact or lesson worth remembering in later runs. Keep it short and specific.

        Args:
            content: The note to keep (at most 4000 characters).
            shared: True to let every agent read it; false keeps it private to you.
            tags: Optional labels such as a topic or customer name.
            expires_in_days: Forget it after this many days; leave empty to keep it.
        """
        if not content.strip():
            return "Nothing saved: the note is empty."
        expires_at = (
            datetime.now(UTC) + timedelta(days=expires_in_days)
            if expires_in_days and expires_in_days > 0
            else None
        )
        item = await store.save(
            content.strip()[:MAX_CONTENT],
            scope="shared" if shared else "agent",
            owner=agent_id,
            tags=[t.strip()[:50] for t in (tags or []) if t.strip()][:10],
            created_by=f"agent:{agent_id}",
            expires_at=expires_at,
        )
        return f"Saved ({item.scope} memory {item.id})."

    return [memory_search, memory_save]
