from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.sync.engine import SyncEngine


class SyncScheduler:
    def __init__(self, engine: SyncEngine, interval_minutes: int) -> None:
        self.engine = engine
        self.interval_minutes = interval_minutes
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        if self.scheduler.running:
            return
        self.scheduler.add_job(
            self.engine.run_once,
            "interval",
            minutes=self.interval_minutes,
            id="adguard-sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def trigger_now(self) -> list[int]:
        return await self.engine.run_once()

    def reschedule(self, interval_minutes: int) -> None:
        self.interval_minutes = interval_minutes
        if self.scheduler.get_job("adguard-sync"):
            self.scheduler.reschedule_job(
                "adguard-sync", trigger="interval", minutes=interval_minutes
            )
