from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.config import (
    CONFIG_PATH_ENV,
    DEFAULT_CONFIG_PATH,
    AppConfig,
    ConfigError,
    _apply_tls_env,
    _interpolate,
)

ENV_REF_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
SCOPE_KEYS = (
    "blocklists",
    "allowlists",
    "user_rules",
    "rewrites",
    "upstream_dns",
    "blocked_services",
)


@dataclass(frozen=True)
class ConfigSaveResult:
    config: AppConfig
    restart_required: bool
    backup_path: Path | None


def config_path(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))


def secret_ref(value: str, field_name: str) -> str:
    value = value.strip()
    match = ENV_REF_PATTERN.fullmatch(value)
    if not match:
        raise ConfigError(f"{field_name} must be an environment reference like ${{SECRET_NAME}}")
    return value


def optional_secret_ref(value: str, field_name: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    return secret_ref(value, field_name)


def validate_env_refs(raw: Mapping[str, Any]) -> None:
    missing: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            match = ENV_REF_PATTERN.fullmatch(value.strip())
            if match and match.group(1) not in os.environ:
                missing.append(match.group(1))
        elif isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, Sequence) and not isinstance(value, str):
            for item in value:
                walk(item)

    walk(raw)
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise ConfigError(f"environment variable is required before saving: {names}")


def raw_config_for_validation(raw: Mapping[str, Any]) -> dict[str, Any]:
    data = _interpolate(dict(raw))
    data.setdefault("dashboard_user", os.environ.get("DASHBOARD_USER"))
    if dashboard_password := os.environ.get("DASHBOARD_PASSWORD"):
        data.setdefault("dashboard_password", dashboard_password)
    if database_path := os.environ.get("DATABASE_PATH"):
        data["database_path"] = database_path
    _apply_tls_env(data)
    return data


def validate_raw_config(raw: Mapping[str, Any]) -> AppConfig:
    validate_env_refs(raw)
    try:
        return AppConfig.model_validate(raw_config_for_validation(raw))
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(str(exc)) from exc


def load_raw_config(path: str | Path | None = None) -> dict[str, Any]:
    target = config_path(path)
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ConfigError(f"could not read config file {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config file must contain a YAML mapping")
    return raw


def dump_config(raw: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(raw), sort_keys=False)


FormValue = str | list[str]


def _form_bool(form: Mapping[str, FormValue], key: str) -> bool:
    return form.get(key) == "on"


def _form_text(form: Mapping[str, FormValue], key: str, default: str = "") -> str:
    value = form.get(key, default)
    if isinstance(value, list):
        value = value[-1] if value else default
    return str(value).strip()


def _form_int(form: Mapping[str, FormValue], key: str, default: str) -> int:
    value = _form_text(form, key, default)
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a whole number") from exc


def _existing_secret_ref(raw: Mapping[str, Any], *path: str) -> str:
    current: Any = raw
    for key in path:
        if not isinstance(current, Mapping):
            raise ConfigError(f"missing password reference at {'.'.join(path)}")
        current = current.get(key)
    if isinstance(current, str):
        return secret_ref(current, ".".join(path))
    raise ConfigError(f"missing password reference at {'.'.join(path)}")


def raw_config_from_form(
    form: Mapping[str, FormValue],
    existing_raw: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    existing_raw = existing_raw or {}
    primary = {
        "name": _form_text(form, "primary_name"),
        "url": _form_text(form, "primary_url"),
        "username": _form_text(form, "primary_username"),
        "password": _existing_secret_ref(existing_raw, "primary", "password"),
        "verify_ssl": _form_bool(form, "primary_verify_ssl"),
    }

    followers: list[dict[str, Any]] = []
    names = form.get("follower_name", [])
    urls = form.get("follower_url", [])
    usernames = form.get("follower_username", [])
    verify_ssl_values = set(form.get("follower_verify_ssl", []))
    existing_followers = existing_raw.get("followers", [])
    if not isinstance(existing_followers, list):
        existing_followers = []
    if isinstance(names, str):
        names = [names]
    if isinstance(urls, str):
        urls = [urls]
    if isinstance(usernames, str):
        usernames = [usernames]

    for index, name in enumerate(names):
        if not str(name).strip():
            continue
        existing_follower = existing_followers[index] if index < len(existing_followers) else {}
        if not isinstance(existing_follower, Mapping):
            existing_follower = {}
        followers.append(
            {
                "name": str(name).strip(),
                "url": str(urls[index]).strip() if index < len(urls) else "",
                "username": str(usernames[index]).strip() if index < len(usernames) else "",
                "password": _existing_secret_ref(existing_follower, "password"),
                "verify_ssl": str(index) in verify_ssl_values,
            }
        )

    raw: dict[str, Any] = {
        "interval_minutes": _form_int(form, "interval_minutes", "5"),
        "dry_run": _form_bool(form, "dry_run"),
        "database_path": _form_text(form, "database_path", "/data/adguard-sync.db"),
        "history_retention_days": _form_int(form, "history_retention_days", "14"),
        "log_level": _form_text(form, "log_level", "INFO").upper(),
        "tls": {
            "enabled": _form_bool(form, "tls_enabled"),
            "cert_file": _form_text(form, "tls_cert_file") or None,
            "key_file": _form_text(form, "tls_key_file") or None,
        },
        "primary": primary,
        "followers": followers,
        "scope": {
            key: {
                "enabled": _form_bool(form, f"scope_{key}_enabled"),
                "prune": _form_bool(form, f"scope_{key}_prune"),
            }
            for key in SCOPE_KEYS
        },
    }
    dashboard_user = _form_text(form, "dashboard_user")
    if dashboard_user:
        raw["dashboard_user"] = dashboard_user
        existing_dashboard_password = existing_raw.get("dashboard_password")
        if isinstance(existing_dashboard_password, str) and existing_dashboard_password.strip():
            raw["dashboard_password"] = secret_ref(
                existing_dashboard_password,
                "dashboard password",
            )
    return raw


class ConfigManager:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = config_path(path)

    def load_raw(self) -> dict[str, Any]:
        return load_raw_config(self.path)

    def save(self, raw: Mapping[str, Any], current_config: AppConfig) -> ConfigSaveResult:
        config = validate_raw_config(raw)
        restart_required = (
            config.database_path != current_config.database_path or config.tls != current_config.tls
        )
        backup_path: Path | None = None
        try:
            if self.path.exists():
                stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
                backup_path = self.path.with_name(f"{self.path.name}.{stamp}.bak")
                shutil.copy2(self.path, backup_path)
            self.path.write_text(dump_config(raw), encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"could not write config file {self.path}: {exc}") from exc
        return ConfigSaveResult(
            config=config, restart_required=restart_required, backup_path=backup_path
        )
