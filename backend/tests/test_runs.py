"""RunManager unit tests: eviction policy, project drop, and exactly-once
event delivery when emits interleave with history replay."""

import asyncio
import contextlib

from planlint.api import runs as runs_module
from planlint.api.runs import RunManager
from planlint.models import RunEvent


def _event(index: int, stage: str = "verify") -> RunEvent:
    return RunEvent(stage=stage, message=f"event-{index}")


async def _finish(manager: RunManager, run_id: str) -> None:
    await manager.emit(run_id, RunEvent(stage="done", message="done"))


async def test_ttl_prunes_expired_done_runs():
    manager = RunManager()
    old = manager.create("run-old", "proj-1")
    await _finish(manager, "run-old")
    old.created_at -= runs_module.DONE_TTL_SECONDS + 1

    manager.create("run-new", "proj-1")
    assert manager.get("run-old") is None
    assert manager.get("run-new") is not None


async def test_cap_evicts_oldest_done_never_live(monkeypatch):
    monkeypatch.setattr(runs_module, "MAX_RUNS", 3)
    manager = RunManager()
    manager.create("run-live", "proj-1")  # never finished
    manager.create("run-done-1", "proj-1")
    await _finish(manager, "run-done-1")
    manager.create("run-done-2", "proj-1")
    await _finish(manager, "run-done-2")

    manager.create("run-4", "proj-1")  # at cap: oldest done run goes
    assert manager.get("run-done-1") is None
    assert manager.get("run-live") is not None
    assert manager.get("run-done-2") is not None
    assert manager.get("run-4") is not None


async def test_drop_project_cancels_task_and_unblocks_subscribers():
    manager = RunManager()
    run = manager.create("run-1", "proj-1")
    run.task = asyncio.create_task(asyncio.sleep(3600))
    await manager.emit("run-1", _event(0))

    async def consume() -> list[str]:
        return [event.message async for event in manager.subscribe("run-1")]

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # let the consumer drain history and block

    manager.drop_project("proj-1")
    received = await asyncio.wait_for(consumer, timeout=5)
    assert received == ["event-0"]
    assert manager.get("run-1") is None
    with contextlib.suppress(asyncio.CancelledError):
        await run.task
    assert run.task.cancelled()


async def test_drop_project_leaves_other_projects_alone():
    manager = RunManager()
    manager.create("run-a", "proj-a")
    manager.create("run-b", "proj-b")
    manager.drop_project("proj-a")
    assert manager.get("run-a") is None
    assert manager.get("run-b") is not None


async def test_emit_after_eviction_is_a_noop():
    manager = RunManager()
    manager.create("run-1", "proj-1")
    manager.drop_project("proj-1")
    await manager.emit("run-1", _event(0))  # must not raise


async def test_exactly_once_delivery_with_interleaved_emit():
    """An emit landing while a slow subscriber is replaying history must be
    delivered exactly once (the old implementation could deliver it twice)."""
    manager = RunManager()
    manager.create("run-1", "proj-1")
    await manager.emit("run-1", _event(0))
    await manager.emit("run-1", _event(1))

    received: list[str] = []

    async def slow_consume() -> None:
        async for event in manager.subscribe("run-1"):
            received.append(event.message)
            await asyncio.sleep(0.01)  # suspend mid-replay so emits interleave

    consumer = asyncio.create_task(slow_consume())
    await asyncio.sleep(0.005)  # consumer is now suspended inside replay
    await manager.emit("run-1", _event(2))
    await _finish(manager, "run-1")
    await asyncio.wait_for(consumer, timeout=5)

    assert received == ["event-0", "event-1", "event-2", "done"]


async def test_late_subscriber_gets_full_history_replay():
    manager = RunManager()
    manager.create("run-1", "proj-1")
    await manager.emit("run-1", _event(0))
    await _finish(manager, "run-1")

    received = [event.message async for event in manager.subscribe("run-1")]
    assert received == ["event-0", "done"]
