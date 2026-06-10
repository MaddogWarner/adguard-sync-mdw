from __future__ import annotations

import httpx
import pytest
import respx

from app.adguard.client import AdGuardClient
from app.config import AppConfig, HostConfig
from app.storage import Storage
from app.sync.engine import SyncEngine


def make_config(tmp_path, *, dry_run: bool = False) -> AppConfig:
    return AppConfig(
        interval_minutes=5,
        dry_run=dry_run,
        database_path=str(tmp_path / "adguard-sync.db"),
        primary=HostConfig(
            name="primary",
            url="http://primary.local",
            username="admin",
            password="secret",
            verify_ssl=True,
        ),
        followers=[
            HostConfig(
                name="follower-a",
                url="http://follower-a.local",
                username="admin",
                password="secret",
                verify_ssl=True,
            )
        ],
    )


def make_engine(config: AppConfig, storage: Storage) -> SyncEngine:
    clients = {
        config.primary.name: AdGuardClient(config.primary),
        **{follower.name: AdGuardClient(follower) for follower in config.followers},
    }
    return SyncEngine(config, storage, clients)


async def close_engine(engine: SyncEngine) -> None:
    for client in engine.clients.values():
        await client.aclose()


def mock_snapshot(router, base_url: str, *, filters=None, blocked_ids=None):
    filters = filters or []
    router.get(f"{base_url}/control/status").mock(
        return_value=httpx.Response(200, json={"version": "v1"})
    )
    router.get(f"{base_url}/control/filtering/status").mock(
        return_value=httpx.Response(
            200,
            json={"filters": filters, "whitelist_filters": [], "user_rules": []},
        )
    )
    router.get(f"{base_url}/control/rewrite/list").mock(return_value=httpx.Response(200, json=[]))
    router.get(f"{base_url}/control/dns_info").mock(return_value=httpx.Response(200, json={}))
    router.get(f"{base_url}/control/blocked_services/get").mock(
        return_value=httpx.Response(200, json={"ids": blocked_ids or [], "schedule": None})
    )


@pytest.mark.asyncio
async def test_engine_applies_representative_diff(tmp_path):
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    engine = make_engine(config, storage)
    with respx.mock as router:
        mock_snapshot(
            router,
            "http://primary.local",
            filters=[{"url": "https://a.test/list.txt", "name": "A", "enabled": True}],
            blocked_ids=["facebook"],
        )
        mock_snapshot(router, "http://follower-a.local")
        add = router.post("http://follower-a.local/control/filtering/add_url").mock(
            return_value=httpx.Response(200)
        )
        refresh = router.post("http://follower-a.local/control/filtering/refresh").mock(
            return_value=httpx.Response(200)
        )
        blocked = router.put("http://follower-a.local/control/blocked_services/update").mock(
            return_value=httpx.Response(200)
        )

        await engine.run_once()
    await close_engine(engine)

    latest = storage.latest_run_per_follower()[0]
    assert latest["status"] == "drift_corrected"
    assert add.called
    assert refresh.called
    assert blocked.called
    health = {row["name"]: row for row in storage.host_health()}
    assert health["primary"]["status"] == "online"
    assert health["follower-a"]["status"] == "online"


@pytest.mark.asyncio
async def test_engine_in_sync_issues_no_writes(tmp_path):
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    engine = make_engine(config, storage)
    same_filters = [{"url": "https://a.test/list.txt", "name": "A", "enabled": True}]
    with respx.mock as router:
        mock_snapshot(router, "http://primary.local", filters=same_filters)
        mock_snapshot(router, "http://follower-a.local", filters=same_filters)

        await engine.run_once()
    await close_engine(engine)

    latest = storage.latest_run_per_follower()[0]
    assert latest["status"] == "in_sync"
    assert latest["added"] == 0


@pytest.mark.asyncio
async def test_engine_dry_run_issues_zero_write_calls_but_records_drift(tmp_path):
    config = make_config(tmp_path, dry_run=True)
    storage = Storage(config.database_path)
    storage.init_db()
    engine = make_engine(config, storage)
    with respx.mock(assert_all_called=False) as router:
        mock_snapshot(
            router,
            "http://primary.local",
            filters=[{"url": "https://a.test/list.txt", "name": "A", "enabled": True}],
        )
        mock_snapshot(router, "http://follower-a.local")
        add = router.post("http://follower-a.local/control/filtering/add_url").mock(
            return_value=httpx.Response(200)
        )

        await engine.run_once()
    await close_engine(engine)

    latest = storage.latest_run_per_follower()[0]
    assert latest["status"] == "drift_detected"
    assert storage.current_drift()
    assert not add.called


@pytest.mark.asyncio
async def test_unreachable_follower_does_not_stop_others(tmp_path):
    config = make_config(tmp_path)
    config.followers.append(
        HostConfig(
            name="follower-b",
            url="http://follower-b.local",
            username="admin",
            password="secret",
            verify_ssl=True,
        )
    )
    storage = Storage(config.database_path)
    storage.init_db()
    engine = make_engine(config, storage)
    with respx.mock as router:
        mock_snapshot(router, "http://primary.local")
        router.get("http://follower-a.local/control/status").mock(
            side_effect=httpx.ConnectError("down")
        )
        mock_snapshot(router, "http://follower-b.local")

        await engine.run_once()
    await close_engine(engine)

    statuses = {row["follower"]: row["status"] for row in storage.latest_run_per_follower()}
    assert statuses == {"follower-a": "failed", "follower-b": "in_sync"}
    health = {row["name"]: row for row in storage.host_health()}
    assert health["follower-a"]["status"] == "offline"
    assert health["follower-a"]["error"] == "down"
    assert health["follower-b"]["status"] == "online"


@pytest.mark.asyncio
async def test_primary_auth_failure_records_offline_health(tmp_path):
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    engine = make_engine(config, storage)
    with respx.mock as router:
        router.get("http://primary.local/control/status").mock(
            return_value=httpx.Response(401, text="Unauthorised")
        )

        await engine.run_once()
    await close_engine(engine)

    health = storage.host_health()
    assert health[0]["name"] == "primary"
    assert health[0]["status"] == "offline"
    assert "HTTP 401" in health[0]["error"]
