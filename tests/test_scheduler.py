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
    scheduler = SyncScheduler(engine, interval_minutes=5)

    result = await scheduler.trigger_now()

    assert result == [1]
    assert engine.calls == 1
