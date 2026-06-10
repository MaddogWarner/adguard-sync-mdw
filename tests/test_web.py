from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig, HostConfig
from app.config_manager import ConfigManager, dump_config
from app.runtime import AppRuntime
from app.storage import Storage
from app.sync.result import Domain, DriftItem
from app.web.routes import create_router


def make_config(tmp_path, *, config_path=None) -> AppConfig:
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


def write_raw_config(tmp_path, *, primary_ref="${ADGUARD_PRIMARY_PASSWORD}"):
    path = tmp_path / "config.yaml"
    path.write_text(
        dump_config(
            {
                "interval_minutes": 5,
                "dry_run": True,
                "database_path": str(tmp_path / "adguard-sync.db"),
                "history_retention_days": 14,
                "log_level": "INFO",
                "tls": {"enabled": True, "cert_file": None, "key_file": None},
                "primary": {
                    "name": "primary",
                    "url": "http://primary.local",
                    "username": "admin",
                    "password": primary_ref,
                    "verify_ssl": True,
                },
                "followers": [
                    {
                        "name": "follower-a",
                        "url": "http://follower-a.local",
                        "username": "admin",
                        "password": "${ADGUARD_FOLLOWER_A_PASSWORD}",
                        "verify_ssl": True,
                    }
                ],
                "scope": {
                    "blocklists": {"enabled": True, "prune": True},
                    "allowlists": {"enabled": True, "prune": True},
                    "user_rules": {"enabled": True, "prune": False},
                    "rewrites": {"enabled": True, "prune": True},
                    "upstream_dns": {"enabled": True, "prune": False},
                    "blocked_services": {"enabled": True, "prune": False},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def make_client(config: AppConfig, storage: Storage, config_path=None) -> TestClient:
    async def trigger_sync() -> list[int]:
        return [1]

    runtime = AppRuntime.build(
        config, ConfigManager(config_path or write_raw_config(storage.path.parent))
    )
    runtime.storage = storage
    runtime.scheduler.storage = storage
    runtime.scheduler.trigger_now = trigger_sync
    app = FastAPI()
    app.include_router(create_router(runtime=runtime))
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
    assert (
        "Settings page added, as per request from cry baby Stinson, who's bad at IT."
        in response.text
    )
    assert "v1.1.0" in response.text
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


def test_settings_page_renders_current_config_without_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("ADGUARD_PRIMARY_PASSWORD", "primary-secret")
    monkeypatch.setenv("ADGUARD_FOLLOWER_A_PASSWORD", "follower-secret")
    config_path = write_raw_config(tmp_path)
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    client = make_client(config, storage, config_path)

    response = client.get("/settings")

    assert response.status_code == 200
    assert "Runtime" in response.text
    assert "Primary" in response.text
    assert "Followers" in response.text
    assert "Passwords must be set via the .env file." in response.text
    assert "primary_password_ref" not in response.text
    assert "follower_password_ref" not in response.text
    assert "dashboard_password_ref" not in response.text
    assert "${ADGUARD_PRIMARY_PASSWORD}" not in response.text
    assert "primary-secret" not in response.text


def test_settings_save_updates_yaml_and_applies_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("ADGUARD_PRIMARY_PASSWORD", "primary-secret")
    monkeypatch.setenv("ADGUARD_FOLLOWER_A_PASSWORD", "follower-secret")
    config_path = write_raw_config(tmp_path)
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    runtime = AppRuntime.build(config, ConfigManager(config_path))
    runtime.storage = storage
    runtime.scheduler.storage = storage
    app = FastAPI()
    app.include_router(create_router(runtime=runtime))
    client = TestClient(app)

    response = client.post(
        "/settings",
        data={
            "interval_minutes": "10",
            "dry_run": "on",
            "history_retention_days": "21",
            "log_level": "WARNING",
            "database_path": config.database_path,
            "tls_enabled": "on",
            "tls_cert_file": "",
            "tls_key_file": "",
            "dashboard_user": "",
            "primary_name": "primary",
            "primary_url": "http://primary.local",
            "primary_username": "admin",
            "primary_verify_ssl": "on",
            "follower_name": "follower-a",
            "follower_url": "http://follower-a.local",
            "follower_username": "admin",
            "follower_verify_ssl": "0",
            "scope_blocklists_enabled": "on",
            "scope_blocklists_prune": "on",
            "scope_allowlists_enabled": "on",
            "scope_allowlists_prune": "on",
            "scope_user_rules_enabled": "on",
            "scope_rewrites_enabled": "on",
            "scope_rewrites_prune": "on",
            "scope_upstream_dns_enabled": "on",
            "scope_blocked_services_enabled": "on",
        },
    )

    assert response.status_code == 200
    assert "Settings saved and applied." in response.text
    assert runtime.config.interval_minutes == 10
    assert runtime.config.history_retention_days == 21
    assert runtime.scheduler.interval_minutes == 10
    text = config_path.read_text(encoding="utf-8")
    assert "${ADGUARD_PRIMARY_PASSWORD}" in text
    assert "primary-secret" not in text
    assert list(tmp_path.glob("config.yaml.*.bak"))


def test_settings_invalid_save_does_not_overwrite_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ADGUARD_PRIMARY_PASSWORD", "primary-secret")
    monkeypatch.setenv("ADGUARD_FOLLOWER_A_PASSWORD", "follower-secret")
    config_path = write_raw_config(tmp_path)
    before = config_path.read_text(encoding="utf-8")
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    client = make_client(config, storage, config_path)

    response = client.post(
        "/settings",
        data={
            "interval_minutes": "7",
            "history_retention_days": "14",
            "log_level": "INFO",
            "database_path": config.database_path,
            "primary_name": "primary",
            "primary_url": "http://primary.local",
            "primary_username": "admin",
        },
    )

    assert response.status_code == 400
    assert "interval_minutes" in response.text
    assert config_path.read_text(encoding="utf-8") == before
