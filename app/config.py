from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

CONFIG_PATH_ENV = "CONFIG_PATH"
DEFAULT_CONFIG_PATH = "/config/config.yaml"
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    """Raised when configuration cannot be loaded or validated."""


class HostConfig(BaseModel):
    name: str
    url: str
    username: str
    password: SecretStr
    verify_ssl: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("host url must use http or https and include a host")
        return value.rstrip("/")


class TlsConfig(BaseModel):
    enabled: bool = True
    cert_file: str | None = None
    key_file: str | None = None


class ScopeItem(BaseModel):
    enabled: bool = True
    prune: bool = True


class ScopeConfig(BaseModel):
    blocklists: ScopeItem = Field(default_factory=ScopeItem)
    allowlists: ScopeItem = Field(default_factory=ScopeItem)
    user_rules: ScopeItem = Field(default_factory=lambda: ScopeItem(prune=False))
    rewrites: ScopeItem = Field(default_factory=ScopeItem)
    upstream_dns: ScopeItem = Field(default_factory=lambda: ScopeItem(prune=False))
    blocked_services: ScopeItem = Field(default_factory=lambda: ScopeItem(prune=False))


class AppConfig(BaseModel):
    interval_minutes: int
    dry_run: bool = True
    tls: TlsConfig = Field(default_factory=TlsConfig)
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    primary: HostConfig
    followers: list[HostConfig]
    dashboard_user: str | None = None
    dashboard_password: SecretStr | None = None
    database_path: str = "/data/adguard-sync.db"
    history_retention_days: int = 14
    log_level: str = "INFO"

    @field_validator("interval_minutes")
    @classmethod
    def validate_interval(cls, value: int) -> int:
        if value not in {5, 10, 15}:
            raise ValueError("interval_minutes must be one of 5, 10, or 15")
        return value

    @field_validator("history_retention_days")
    @classmethod
    def validate_history_retention(cls, value: int) -> int:
        if value < 1:
            raise ValueError("history_retention_days must be at least 1")
        return value

    @model_validator(mode="after")
    def validate_hosts(self) -> AppConfig:
        if not self.followers:
            raise ValueError("at least one follower is required")

        names = [self.primary.name, *(follower.name for follower in self.followers)]
        if len(names) != len(set(names)):
            raise ValueError("host names must be unique")

        follower_urls = {follower.url for follower in self.followers}
        if self.primary.url in follower_urls:
            raise ValueError("follower url must not equal primary url")

        if len(follower_urls) != len(self.followers):
            raise ValueError("follower urls must be unique")

        if bool(self.dashboard_user) ^ bool(self.dashboard_password):
            raise ValueError("dashboard_user and dashboard_password must be set together")

        if bool(self.tls.cert_file) ^ bool(self.tls.key_file):
            raise ValueError("tls.cert_file and tls.key_file must be set together")
        return self


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            env_name = match.group(1)
            if env_name not in os.environ:
                raise ConfigError(f"environment variable {env_name} is required")
            return os.environ[env_name]

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_interpolate(item) for item in value]
    if isinstance(value, dict):
        return {key: _interpolate(item) for key, item in value.items()}
    return value


_TRUTHY = {"1", "true", "yes", "on"}


def _apply_tls_env(data: dict[str, Any]) -> None:
    tls = data.get("tls")
    tls = dict(tls) if isinstance(tls, dict) else {}
    if (enabled := os.environ.get("TLS_ENABLED")) is not None:
        tls["enabled"] = enabled.strip().lower() in _TRUTHY
    if cert_file := os.environ.get("TLS_CERT_FILE"):
        tls["cert_file"] = cert_file
    if key_file := os.environ.get("TLS_KEY_FILE"):
        tls["key_file"] = key_file
    if tls:
        data["tls"] = tls


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path or os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except IsADirectoryError as exc:
        raise ConfigError(
            f"{config_path} is a directory, not a file. "
            "Docker created it automatically because the host-side file did not exist before the "
            "container started. Stop the container, delete that directory, create the file "
            "(e.g. copy config.example.yaml to config.yaml), then start again."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"could not read config file {config_path}: {exc}") from exc
    try:
        data = _interpolate(raw)
        data.setdefault("dashboard_user", os.environ.get("DASHBOARD_USER"))
        if dashboard_password := os.environ.get("DASHBOARD_PASSWORD"):
            data.setdefault("dashboard_password", dashboard_password)
        if database_path := os.environ.get("DATABASE_PATH"):
            data["database_path"] = database_path
        _apply_tls_env(data)
        return AppConfig.model_validate(data)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(str(exc)) from exc
