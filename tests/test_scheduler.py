from __future__ import annotations

import pytest

from app.scheduler import SyncScheduler


class DummyEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def run_once(self) -> list[int]:
        self.calls += 1
        return [self.calls]


@pytest.mark.asyncio
async def test_trigger_now_invokes_engine_once():
    engine = DummyEngine()
    scheduler = SyncScheduler(
        engine,
        interval_minutes=5,
        storage=DummyStorage(),
        history_retention_days=14,
    )

    result = await scheduler.trigger_now()

    assert result == [1]
    assert engine.calls == 1


class DummyStorage:
    def __init__(self) -> None:
        self.retention_days: int | None = None

    def purge_history_older_than(self, retention_days: int) -> int:
        self.retention_days = retention_days
        return 3


def test_purge_history_invokes_storage_retention():
    storage = DummyStorage()
    scheduler = SyncScheduler(
        DummyEngine(),
        interval_minutes=5,
        storage=storage,
        history_retention_days=14,
    )

    assert scheduler.purge_history() == 3
    assert storage.retention_days == 14


@pytest.mark.asyncio
async def test_concurrent_triggers_are_serialised():
    import asyncio

    class SlowEngine:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def run_once(self) -> list[int]:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return []

    engine = SlowEngine()
    scheduler = SyncScheduler(
        engine, interval_minutes=5, storage=DummyStorage(), history_retention_days=14
    )

    await asyncio.gather(scheduler.trigger_now(), scheduler.trigger_now(), scheduler._run_locked())

    assert engine.max_active == 1
