from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig, HostConfig
from app.storage import Storage
from app.sync.result import Domain, DriftItem
from app.web.routes import create_router


def make_config(tmp_path) -> AppConfig:
    return AppConfig(
        interval_minutes=5,
        dry_run=True,
        database_path=str(tmp_path / "adguard-sync.db"),
        primary=HostConfig(
            name="primary", url="http://primary.local", username="admin", password="x"
        ),
        followers=[
            HostConfig(
                name="follower-a", url="http://follower-a.local", username="admin", password="x"
            )
        ],
    )


def make_client(config: AppConfig, storage: Storage) -> TestClient:
    async def trigger_sync() -> list[int]:
        return [1]

    app = FastAPI()
    app.include_router(create_router(config=config, storage=storage, trigger_sync=trigger_sync))
    return TestClient(app)


def test_healthz_and_status_page_render(tmp_path):
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    storage.record_run(follower="follower-a", status="in_sync")
    storage.record_host_health(
        name="primary",
        role="primary",
        url="http://primary.local",
        online=True,
        last_checked="2026-06-10T00:00:00+00:00",
    )
    storage.record_host_health(
        name="follower-a",
        role="follower",
        url="http://follower-a.local",
        online=False,
        last_checked="2026-06-10T00:00:01+00:00",
        error="follower unreachable",
    )
    client = make_client(config, storage)

    assert client.get("/healthz").json() == {"status": "ok"}
    response = client.get("/")
    assert response.status_code == 200
    assert "DRY RUN" in response.text
    assert "follower-a" in response.text
    assert "Configured DNS Hosts" in response.text
    assert "primary" in response.text
    assert "http://primary.local" in response.text
    assert "online" in response.text
    assert "offline" in response.text
    assert "follower unreachable" in response.text
    assert "MaddogWarner/adguard-sync-mdw" in response.text
    assert "maddogwarner.com" in response.text
    assert 'hx-post="/sync-now"' in response.text
    assert 'hx-post="/api/sync"' not in response.text

    status = client.get("/api/status").json()
    assert status["host_health"][0]["name"] == "primary"
    assert status["host_health"][0]["status"] == "online"


def test_dashboard_sync_now_renders_html_result_without_api_json(tmp_path):
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    client = make_client(config, storage)

    api_response = client.post("/api/sync")
    assert api_response.json() == {"run_ids": [1]}

    response = client.post("/sync-now")

    assert response.status_code == 200
    assert "Sync completed. Run ID: 1" in response.text
    assert '{"run_ids"' not in response.text
    assert "Configured DNS Hosts" in response.text
    assert "Sync Runs" in response.text


def test_drift_page_renders_expandable_primary_and_follower_values(tmp_path):
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    run_id = storage.record_run(follower="follower-a", status="drift_detected")
    storage.record_drift(
        run_id,
        "follower-a",
        [
            DriftItem(
                Domain.BLOCKLISTS,
                "changed",
                "https://adguardteam.github.io/HostlistsRegistry/assets/filter_2.txt",
                {
                    "name": "AdAway Default Blocklist",
                    "url": "https://adguardteam.github.io/HostlistsRegistry/assets/filter_2.txt",
                    "enabled": True,
                },
                {
                    "name": "AdAway Default Blocklist",
                    "url": "https://adguardteam.github.io/HostlistsRegistry/assets/filter_2.txt",
                    "enabled": False,
                },
            )
        ],
    )
    client = make_client(config, storage)

    response = client.get("/drift")

    assert response.status_code == 200
    assert "Show difference" in response.text
    assert "Primary" in response.text
    assert "Follower" in response.text
    assert "AdAway Default Blocklist" in response.text
    assert "True" in response.text
    assert "False" in response.text


def test_drift_page_renders_missing_side_labels(tmp_path):
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    run_id = storage.record_run(follower="follower-a", status="drift_detected")
    storage.record_drift(
        run_id,
        "follower-a",
        [
            DriftItem(
                Domain.REWRITES,
                "extra",
                "typo.example -> 192.168.1.50",
                None,
                {"domain": "typo.example", "answer": "192.168.1.50"},
            )
        ],
    )
    client = make_client(config, storage)

    response = client.get("/drift")

    assert response.status_code == 200
    assert "Not present on primary" in response.text
    assert "typo.example" in response.text
    assert "192.168.1.50" in response.text
