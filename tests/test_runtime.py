from __future__ import annotations

import pytest

from app.config import AppConfig, HostConfig
from app.config_manager import ConfigManager
from app.runtime import AppRuntime
from app.storage import Storage


class DummyClient:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class DummyScheduler:
    def __init__(self) -> None:
        self.engine = None
        self.interval_minutes = None
        self.history_retention_days = None

    def configure(self, *, engine, interval_minutes: int, history_retention_days: int) -> None:
        self.engine = engine
        self.interval_minutes = interval_minutes
        self.history_retention_days = history_retention_days


def make_config(tmp_path, interval_minutes: int) -> AppConfig:
    return AppConfig(
        interval_minutes=interval_minutes,
        dry_run=True,
        database_path=str(tmp_path / "adguard-sync.db"),
        history_retention_days=14,
        primary=HostConfig(
            name="primary",
            url="http://primary.local",
            username="admin",
            password="secret",
        ),
        followers=[
            HostConfig(
                name="follower-a",
                url="http://follower-a.local",
                username="admin",
                password="secret",
            )
        ],
    )


@pytest.mark.asyncio
async def test_runtime_apply_config_replaces_engine_scheduler_and_closes_old_clients(
    tmp_path, monkeypatch
):
    old_client = DummyClient()
    new_client = DummyClient()
    scheduler = DummyScheduler()
    config = make_config(tmp_path, 5)
    runtime = AppRuntime(
        config=config,
        storage=Storage(config.database_path),
        clients={"primary": old_client},
        engine=object(),
        scheduler=scheduler,
        config_manager=ConfigManager(tmp_path / "config.yaml"),
    )
    monkeypatch.setattr("app.runtime.build_clients", lambda _config: {"primary": new_client})
    new_config = make_config(tmp_path, 10)

    await runtime.apply_config(new_config)

    assert runtime.config.interval_minutes == 10
    assert runtime.clients == {"primary": new_client}
    assert scheduler.interval_minutes == 10
    assert scheduler.history_retention_days == 14
    assert scheduler.engine is runtime.engine
    assert old_client.closed is True
    assert new_client.closed is False
