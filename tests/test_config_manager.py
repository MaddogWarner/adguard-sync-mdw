from __future__ import annotations

import pytest

from app.config import AppConfig, ConfigError, HostConfig
from app.config_manager import ConfigManager, dump_config, raw_config_from_form, validate_raw_config


def raw_config(tmp_path, primary_password="${ADGUARD_PRIMARY_PASSWORD}"):
    return {
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
            "password": primary_password,
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


def current_config(tmp_path) -> AppConfig:
    return AppConfig(
        interval_minutes=5,
        dry_run=True,
        database_path=str(tmp_path / "adguard-sync.db"),
        primary=HostConfig(
            name="primary",
            url="http://primary.local",
            username="admin",
            password="primary-secret",
        ),
        followers=[
            HostConfig(
                name="follower-a",
                url="http://follower-a.local",
                username="admin",
                password="follower-secret",
            )
        ],
    )


def test_validate_raw_config_resolves_env_refs_without_exposing_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ADGUARD_PRIMARY_PASSWORD", "primary-secret")
    monkeypatch.setenv("ADGUARD_FOLLOWER_A_PASSWORD", "follower-secret")

    config = validate_raw_config(raw_config(tmp_path))

    assert config.primary.password.get_secret_value() == "primary-secret"


def test_validate_raw_config_rejects_missing_env_ref(tmp_path):
    with pytest.raises(ConfigError, match="ADGUARD_PRIMARY_PASSWORD"):
        validate_raw_config(raw_config(tmp_path))


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda raw: raw.__setitem__("interval_minutes", 7), "interval_minutes"),
        (lambda raw: raw["followers"].clear(), "at least one follower"),
        (
            lambda raw: raw["followers"][0].__setitem__("url", "http://primary.local"),
            "follower url",
        ),
        (lambda raw: raw["followers"][0].__setitem__("name", "primary"), "host names"),
        (lambda raw: raw["primary"].__setitem__("url", "not-a-url"), "host url"),
        (
            lambda raw: raw["tls"].__setitem__("cert_file", "/certs/server.crt"),
            "tls.cert_file and tls.key_file",
        ),
    ],
)
def test_validate_raw_config_rejects_invalid_settings(tmp_path, monkeypatch, mutator, message):
    monkeypatch.setenv("ADGUARD_PRIMARY_PASSWORD", "primary-secret")
    monkeypatch.setenv("ADGUARD_FOLLOWER_A_PASSWORD", "follower-secret")
    raw = raw_config(tmp_path)
    mutator(raw)

    with pytest.raises(ConfigError, match=message):
        validate_raw_config(raw)


def test_config_manager_save_writes_yaml_with_backup_and_no_real_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("ADGUARD_PRIMARY_PASSWORD", "primary-secret")
    monkeypatch.setenv("ADGUARD_FOLLOWER_A_PASSWORD", "follower-secret")
    path = tmp_path / "config.yaml"
    path.write_text(dump_config(raw_config(tmp_path)), encoding="utf-8")
    manager = ConfigManager(path)
    raw = raw_config(tmp_path)
    raw["interval_minutes"] = 10

    result = manager.save(raw, current_config(tmp_path))

    text = path.read_text(encoding="utf-8")
    assert "interval_minutes: 10" in text
    assert "${ADGUARD_PRIMARY_PASSWORD}" in text
    assert "primary-secret" not in text
    assert result.backup_path is not None
    assert result.backup_path.exists()


def test_raw_config_from_form_preserves_password_refs_from_existing_config():
    raw = raw_config_from_form(
        {
            "interval_minutes": "5",
            "history_retention_days": "14",
            "log_level": "INFO",
            "database_path": "/data/adguard-sync.db",
            "primary_name": "primary",
            "primary_url": "http://primary.local",
            "primary_username": "admin",
            "follower_name": ["follower-a"],
            "follower_url": ["http://follower-a.local"],
            "follower_username": ["admin"],
        },
        {
            "primary": {"password": "${ADGUARD_PRIMARY_PASSWORD}"},
            "followers": [{"password": "${ADGUARD_FOLLOWER_A_PASSWORD}"}],
        },
    )

    assert raw["primary"]["password"] == "${ADGUARD_PRIMARY_PASSWORD}"
    assert raw["followers"][0]["password"] == "${ADGUARD_FOLLOWER_A_PASSWORD}"


def test_raw_config_from_form_requires_existing_password_refs():
    with pytest.raises(ConfigError, match="missing password reference"):
        raw_config_from_form(
            {
                "interval_minutes": "5",
                "history_retention_days": "14",
                "log_level": "INFO",
                "database_path": "/data/adguard-sync.db",
                "primary_name": "primary",
                "primary_url": "http://primary.local",
                "primary_username": "admin",
            },
            {},
        )
