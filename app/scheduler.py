from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.storage import Storage
from app.sync.engine import SyncEngine


class SyncScheduler:
    def __init__(
        self,
        engine: SyncEngine,
        interval_minutes: int,
        storage: Storage,
        history_retention_days: int,
    ) -> None:
        self.engine = engine
        self.interval_minutes = interval_minutes
        self.storage = storage
        self.history_retention_days = history_retention_days
        self.scheduler = AsyncIOScheduler()
        # Serialises scheduled and manual runs so two never write to a follower at once.
        self._run_lock = asyncio.Lock()

    def start(self) -> None:
        if self.scheduler.running:
            return
        self.scheduler.add_job(
            self._run_locked,
            "interval",
            minutes=self.interval_minutes,
            id="adguard-sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.purge_history,
            "interval",
            days=1,
            id="adguard-sync-history-retention",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def _run_locked(self) -> list[int]:
        async with self._run_lock:
            return await self.engine.run_once()

    async def trigger_now(self) -> list[int]:
        return await self._run_locked()

    def purge_history(self) -> int:
        return self.storage.purge_history_older_than(self.history_retention_days)

    def reschedule(self, interval_minutes: int) -> None:
        self.interval_minutes = interval_minutes
        if self.scheduler.get_job("adguard-sync"):
            self.scheduler.reschedule_job(
                "adguard-sync", trigger="interval", minutes=interval_minutes
            )

    def configure(
        self,
        *,
        engine: SyncEngine,
        interval_minutes: int,
        history_retention_days: int,
    ) -> None:
        self.engine = engine
        self.history_retention_days = history_retention_days
        self.reschedule(interval_minutes)
