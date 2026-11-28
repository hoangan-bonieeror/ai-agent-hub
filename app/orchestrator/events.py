"""In-process fan-out of workflow progress events to SSE subscribers.

Each run gets a channel that keeps its event history, so a client that
connects late (or reconnects with ``Last-Event-ID``) replays what it missed
before receiving live events. Finished channels are kept for
``retention_seconds`` and then dropped.

This lives in the API process. Running several API workers would need a
shared bus (for example Redis streams); the plan puts that in Phase 4 with
the worker pool.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.orchestrator.workflow import WorkflowEvent


@dataclass
class _Channel:
    events: list[WorkflowEvent] = field(default_factory=list)
    closed_at: float | None = None
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)


class RunEventBroker:
    def __init__(self, *, retention_seconds: float = 600, max_events: int = 5000) -> None:
        self.retention_seconds = retention_seconds
        self.max_events = max_events
        self._channels: dict[str, _Channel] = {}

    def open(self, run_id: str) -> None:
        self._prune()
        self._channels.setdefault(run_id, _Channel())

    def has(self, run_id: str) -> bool:
        return run_id in self._channels

    async def publish(self, event: WorkflowEvent) -> None:
        channel = self._channels.get(event.run_id)
        if channel is None or channel.closed_at is not None:
            return
        async with channel.changed:
            if len(channel.events) < self.max_events:
                channel.events.append(event)
            channel.changed.notify_all()

    async def close(self, run_id: str) -> None:
        channel = self._channels.get(run_id)
        if channel is None or channel.closed_at is not None:
            return
        async with channel.changed:
            channel.closed_at = time.monotonic()
            channel.changed.notify_all()

    async def stream(
        self, run_id: str, *, after: int = 0, keepalive_seconds: float = 15
    ) -> AsyncIterator[tuple[int, WorkflowEvent] | None]:
        """Yield ``(sequence, event)`` from event ``after + 1`` on, until the
        run's channel closes. Yields ``None`` after ``keepalive_seconds``
        without events, so the caller can send a keep-alive."""
        channel = self._channels.get(run_id)
        if channel is None:
            return
        seen = max(after, 0)
        while True:
            idle = False
            async with channel.changed:
                try:
                    async with asyncio.timeout(keepalive_seconds):
                        await channel.changed.wait_for(
                            lambda n=seen: len(channel.events) > n or channel.closed_at is not None
                        )
                except TimeoutError:
                    idle = True
                batch = channel.events[seen:]
                closed = channel.closed_at is not None
            if idle and not batch:
                yield None
                continue
            for event in batch:
                seen += 1
                yield seen, event
            if closed and seen >= len(channel.events):
                return

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.retention_seconds
        for run_id, channel in list(self._channels.items()):
            if channel.closed_at is not None and channel.closed_at < cutoff:
                del self._channels[run_id]
