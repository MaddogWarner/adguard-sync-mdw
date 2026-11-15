from __future__ import annotations

import pytest

from app.config import ConfigError, load_config


def write_config(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def base_config(password: str = "secret") -> str:
    return f"""
interval_minutes: 5
dry_run: true
primary:
  name: primary
  url: http://primary.local
  username: admin
  password: {password}
followers:
  - name: follower-a
    url: http://follower-a.local
    username: admin
    password: {password}
scope:
  blocklists:
    enabled: true
    prune: true
"""


def test_valid_config_loads(tmp_path):
    config = load_config(write_config(tmp_path, base_config()))

    assert config.interval_minutes == 5
    assert config.primary.url == "http://primary.local"
    assert config.followers[0].name == "follower-a"


def test_env_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("ADGUARD_PASSWORD", "from-env")

    config = load_config(write_config(tmp_path, base_config("${ADGUARD_PASSWORD}")))

    assert config.primary.password.get_secret_value() == "from-env"


def test_database_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "override.db"))

    config = load_config(write_config(tmp_path, base_config()))

    assert config.database_path == str(tmp_path / "override.db")


def test_missing_env_var_raises(tmp_path):
    with pytest.raises(ConfigError, match="MISSING_SECRET"):
        load_config(write_config(tmp_path, base_config("${MISSING_SECRET}")))


def test_bad_interval_rejected(tmp_path):
    with pytest.raises(ConfigError, match="interval_minutes"):
        config_text = base_config().replace("interval_minutes: 5", "interval_minutes: 7")
        load_config(write_config(tmp_path, config_text))


def test_bad_history_retention_rejected(tmp_path):
    text = base_config().replace("dry_run: true", "dry_run: true\nhistory_retention_days: 0")

    with pytest.raises(ConfigError, match="history_retention_days"):
        load_config(write_config(tmp_path, text))


def test_duplicate_primary_follower_url_rejected(tmp_path):
    text = base_config().replace("url: http://follower-a.local", "url: http://primary.local")

    with pytest.raises(ConfigError, match="follower url"):
        load_config(write_config(tmp_path, text))


def test_tls_enabled_by_default(tmp_path):
    config = load_config(write_config(tmp_path, base_config()))

    assert config.tls.enabled is True
    assert config.tls.cert_file is None
    assert config.tls.key_file is None


def test_tls_cert_without_key_rejected(tmp_path):
    text = base_config().replace(
        "dry_run: true",
        "dry_run: true\ntls:\n  enabled: true\n  cert_file: /tmp/server.crt",
    )

    with pytest.raises(ConfigError, match="tls.cert_file and tls.key_file"):
        load_config(write_config(tmp_path, text))


def test_tls_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("TLS_ENABLED", "false")
    monkeypatch.setenv("TLS_CERT_FILE", "/certs/server.crt")
    monkeypatch.setenv("TLS_KEY_FILE", "/certs/server.key")

    config = load_config(write_config(tmp_path, base_config()))

    assert config.tls.enabled is False
    assert config.tls.cert_file == "/certs/server.crt"
    assert config.tls.key_file == "/certs/server.key"


def test_empty_followers_rejected(tmp_path):
    text = base_config().replace(
        """followers:
  - name: follower-a
    url: http://follower-a.local
    username: admin
    password: secret
""",
        "followers: []\n",
    )

    with pytest.raises(ConfigError, match="at least one follower"):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", []),
        ("  ", []),
        ("https://sync.example", ["https://sync.example"]),
        (
            " https://sync.example , https://sync.example:8443 ",
            ["https://sync.example", "https://sync.example:8443"],
        ),
    ],
)
def test_allowed_origins_environment(tmp_path, monkeypatch, value, expected):
    from app.config_manager import load_raw_config, validate_raw_config

    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", value)
    path = write_config(tmp_path, base_config())
    assert load_config(path).dashboard_allowed_origins == expected
    assert validate_raw_config(load_raw_config(path)).dashboard_allowed_origins == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://*.example",
        "https://sync.example/",
        "https://sync.example/path",
        "https://user:password@sync.example",
        "https://sync.example?",
        "https://sync.example#",
        "https://sync.example:invalid",
        "https://sync.example:70000",
        "null",
        "sync.example",
        "https://sync.example,",
        "https://sync.example,,https://other.example",
        "https://sync.example https://other.example",
        "ftp://sync.example",
    ],
)
def test_invalid_allowed_origins_fail_config_loading(tmp_path, monkeypatch, value):
    from app.config_manager import load_raw_config, validate_raw_config

    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", value)
    path = write_config(tmp_path, base_config())
    with pytest.raises(ConfigError, match="dashboard_allowed_origins"):
        load_config(path)
    with pytest.raises(ConfigError, match="dashboard_allowed_origins"):
        validate_raw_config(load_raw_config(path))


def test_yaml_allowed_origins_and_environment_override(tmp_path, monkeypatch):
    path = write_config(
        tmp_path, base_config() + '\ndashboard_allowed_origins: ["https://sync.example"]\n'
    )
    assert load_config(path).dashboard_allowed_origins == ["https://sync.example"]
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://override.example")
    assert load_config(path).dashboard_allowed_origins == ["https://override.example"]
