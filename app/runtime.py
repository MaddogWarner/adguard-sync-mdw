from __future__ import annotations

from dataclasses import dataclass

from app.adguard.client import AdGuardClient
from app.config import AppConfig
from app.config_manager import ConfigManager
from app.scheduler import SyncScheduler
from app.storage import Storage
from app.sync.engine import SyncEngine


def build_clients(config: AppConfig) -> dict[str, AdGuardClient]:
    return {
        config.primary.name: AdGuardClient(config.primary),
        **{follower.name: AdGuardClient(follower) for follower in config.followers},
    }


@dataclass
class RuntimeReloadResult:
    restart_required: bool


class AppRuntime:
    def __init__(
        self,
        *,
        config: AppConfig,
        storage: Storage,
        clients: dict[str, AdGuardClient],
        engine: SyncEngine,
        scheduler: SyncScheduler,
        config_manager: ConfigManager,
    ) -> None:
        self.config = config
        self.storage = storage
        self.clients = clients
        self.engine = engine
        self.scheduler = scheduler
        self.config_manager = config_manager

    @classmethod
    def build(cls, config: AppConfig, config_manager: ConfigManager) -> AppRuntime:
        storage = Storage(config.database_path)
        storage.init_db()
        storage.purge_history_older_than(config.history_retention_days)
        clients = build_clients(config)
        engine = SyncEngine(config, storage, clients)
        scheduler = SyncScheduler(
            engine,
            config.interval_minutes,
            storage,
            config.history_retention_days,
        )
        return cls(
            config=config,
            storage=storage,
            clients=clients,
            engine=engine,
            scheduler=scheduler,
            config_manager=config_manager,
        )

    async def close_clients(self) -> None:
        for client in self.clients.values():
            await client.aclose()

    async def shutdown(self) -> None:
        self.scheduler.shutdown()
        await self.close_clients()

    async def apply_config(self, config: AppConfig) -> RuntimeReloadResult:
        old_clients = self.clients
        new_clients = build_clients(config)
        new_engine = SyncEngine(config, self.storage, new_clients)

        self.config = config
        self.clients = new_clients
        self.engine = new_engine
        self.scheduler.configure(
            engine=new_engine,
            interval_minutes=config.interval_minutes,
            history_retention_days=config.history_retention_days,
        )

        for client in old_clients.values():
            await client.aclose()

        return RuntimeReloadResult(restart_required=False)
