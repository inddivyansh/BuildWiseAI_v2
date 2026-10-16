"""RunManager: tracks background verification runs and fans events out to
SSE subscribers. New subscribers get the full history replayed first.

Completed runs are pruned (TTL + cap) so the in-memory registry can't grow
without bound; live runs are never evicted."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from planlint.models import RunEvent

_SENTINEL = None

MAX_RUNS = 50
DONE_TTL_SECONDS = 3600


@dataclass
class Run:
    """In-memory state of one verification run: its event history, live SSE
    subscribers, and the background task producing them."""

    run_id: str
    project_id: str
    user_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    history: list[RunEvent] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    done: bool = False
    task: asyncio.Task | None = None


class RunManager:
    """Registry of active and recently-finished runs (see module docstring)."""

    def __init__(self):
        self._runs: dict[str, Run] = {}

    def create(self, run_id: str, project_id: str, user_id: str | None = None) -> Run:
        self._prune()
        run = Run(run_id=run_id, project_id=project_id, user_id=user_id)
        self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def _prune(self) -> None:
        now = time.monotonic()
        expired = []
        for run_id, run in self._runs.items():
            if run.done and now - run.created_at > DONE_TTL_SECONDS:
                expired.append(run_id)
            elif not run.done and now - run.created_at > DONE_TTL_SECONDS:
                # Run is stale/hung; cancel and evict it so it doesn't leak memory.
                if run.task and not run.task.done():
                    run.task.cancel()
                expired.append(run_id)
                
        for run_id in expired:
            del self._runs[run_id]
        if len(self._runs) >= MAX_RUNS:
            done_oldest_first = sorted(
                (run for run in self._runs.values() if run.done),
                key=lambda run: run.created_at,
            )
            for run in done_oldest_first[: len(self._runs) - MAX_RUNS + 1]:
                del self._runs[run.run_id]

    def drop_project(self, project_id: str) -> None:
        """Evict every run belonging to a (deleted) project, cancelling any
        that are still in flight and unblocking their subscribers."""
        for run_id in [
            rid for rid, run in self._runs.items() if run.project_id == project_id
        ]:
            run = self._runs.pop(run_id)
            if not run.done:
                if run.task is not None:
                    run.task.cancel()
                run.done = True
                for queue in run.subscribers:
                    queue.put_nowait(_SENTINEL)

    async def emit(self, run_id: str, event: RunEvent) -> None:
        run = self._runs.get(run_id)
        if run is None:  # evicted mid-run (project deleted); drop silently
            return
        run.history.append(event)
        for queue in run.subscribers:
            queue.put_nowait(event)
        if event.stage in ("done", "error"):
            run.done = True
            for queue in run.subscribers:
                queue.put_nowait(_SENTINEL)

    async def subscribe(self, run_id: str):
        """Async iterator over a run's events: history, then live, until done.

        Replay is index-based and the queue is registered before the catch-up
        snapshot (with no await between), so an emit interleaved with a slow
        replay is delivered exactly once — never duplicated, never missed."""
        run = self._runs[run_id]
        sent = 0
        while sent < len(run.history):
            yield run.history[sent]
            sent += 1
        if run.done:
            return
        queue: asyncio.Queue = asyncio.Queue()
        run.subscribers.append(queue)
        catch_up_end = len(run.history)  # atomic with the append above
        try:
            while sent < catch_up_end:
                yield run.history[sent]
                sent += 1
            while True:
                event = await queue.get()
                if event is _SENTINEL:
                    return
                yield event
        finally:
            run.subscribers.remove(queue)
