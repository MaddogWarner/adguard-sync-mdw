from __future__ import annotations

import json
import secrets
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import AppConfig, ConfigError
from app.config_manager import SCOPE_KEYS, FormValue, raw_config_from_form
from app.runtime import AppRuntime
from app.storage import Storage

templates = Jinja2Templates(directory="app/web/templates")
templates.env.globals["app_version"] = __version__
security = HTTPBasic(auto_error=False)


def auth_dependency(runtime: AppRuntime):
    async def verify(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
    ) -> None:
        config = runtime.config
        if not config.dashboard_user and not config.dashboard_password:
            return
        if credentials is None:
            raise_auth()
        expected_password = (
            config.dashboard_password.get_secret_value() if config.dashboard_password else ""
        )
        user_ok = secrets.compare_digest(credentials.username, config.dashboard_user or "")
        password_ok = secrets.compare_digest(credentials.password, expected_password)
        if not (user_ok and password_ok):
            raise_auth()

    return verify


def raise_auth() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )


def _parse_stored_json(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _display_value(value: Any, missing_label: str) -> dict[str, Any]:
    parsed = _parse_stored_json(value)
    if parsed is None:
        return {"missing": True, "label": missing_label, "rows": [], "text": ""}
    if isinstance(parsed, dict):
        return {
            "missing": False,
            "label": "",
            "rows": [{"key": key, "value": parsed[key]} for key in sorted(parsed)],
            "text": "",
        }
    if isinstance(parsed, list):
        return {
            "missing": False,
            "label": "",
            "rows": [{"key": str(index + 1), "value": item} for index, item in enumerate(parsed)],
            "text": "",
        }
    return {"missing": False, "label": "", "rows": [], "text": str(parsed)}


def format_drift_for_display(drift: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **item,
            "primary_display": _display_value(
                item.get("primary_value"),
                "Not present on primary",
            ),
            "follower_display": _display_value(
                item.get("follower_value"),
                "Not present on follower",
            ),
        }
        for item in drift
    ]


def configured_host_health(config: AppConfig, storage: Storage) -> list[dict[str, Any]]:
    recorded = {item["name"]: item for item in storage.host_health()}
    hosts = [
        {
            "name": config.primary.name,
            "role": "primary",
            "url": config.primary.url,
        },
        *[
            {
                "name": follower.name,
                "role": "follower",
                "url": follower.url,
            }
            for follower in config.followers
        ],
    ]
    result: list[dict[str, Any]] = []
    for host in hosts:
        health = recorded.get(host["name"])
        if health:
            result.append({**host, **health})
        else:
            result.append(
                {
                    **host,
                    "status": "not_checked",
                    "last_checked": "",
                    "error": "Not yet polled",
                }
            )
    return result


def status_context(
    config: AppConfig,
    storage: Storage,
    sync_message: str | None = None,
) -> dict[str, Any]:
    return {
        "dry_run": config.dry_run,
        "host_health": configured_host_health(config, storage),
        "runs": storage.latest_run_per_follower(),
        "sync_message": sync_message,
    }


def _sync_message(run_ids: list[int]) -> str:
    if not run_ids:
        return "Sync completed. No runs were recorded."
    if len(run_ids) == 1:
        return f"Sync completed. Run ID: {run_ids[0]}"
    ids = ", ".join(str(run_id) for run_id in run_ids)
    return f"Sync completed. Run IDs: {ids}"


def _host_form_model(host: Any) -> dict[str, Any]:
    return {
        "name": host.name,
        "url": host.url,
        "username": host.username,
        "verify_ssl": host.verify_ssl,
    }


def _follower_form_models(config: AppConfig) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "name": follower.name,
            "url": follower.url,
            "username": follower.username,
            "verify_ssl": follower.verify_ssl,
        }
        for index, follower in enumerate(config.followers)
    ]


def settings_context(
    runtime: AppRuntime,
    *,
    message: str | None = None,
    error: str | None = None,
    restart_required: bool = False,
) -> dict[str, Any]:
    config = runtime.config
    scope = {key: getattr(config.scope, key) for key in SCOPE_KEYS}
    return {
        "dry_run": config.dry_run,
        "message": message,
        "error": error,
        "restart_required": restart_required,
        "config": config,
        "primary": _host_form_model(config.primary),
        "followers": _follower_form_models(config),
        "scope": scope,
        "scope_keys": SCOPE_KEYS,
    }


async def _urlencoded_form(request: Request) -> dict[str, FormValue]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    result: dict[str, FormValue] = {}
    list_keys = {
        "follower_name",
        "follower_url",
        "follower_username",
        "follower_verify_ssl",
    }
    for key, values in parsed.items():
        result[key] = values if key in list_keys else values[-1]
    return result


def create_router(*, runtime: AppRuntime) -> APIRouter:
    router = APIRouter()
    protected = [Depends(auth_dependency(runtime))]

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/", response_class=HTMLResponse, dependencies=protected)
    async def status_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request,
            "status.html",
            status_context(runtime.config, runtime.storage),
        )

    @router.post("/sync-now", response_class=HTMLResponse, dependencies=protected)
    async def sync_now(request: Request) -> Any:
        run_ids = await runtime.scheduler.trigger_now()
        return templates.TemplateResponse(
            request,
            "status_content.html",
            status_context(runtime.config, runtime.storage, _sync_message(run_ids)),
        )

    @router.get("/settings", response_class=HTMLResponse, dependencies=protected)
    async def settings_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request,
            "settings.html",
            settings_context(runtime),
        )

    @router.post("/settings", response_class=HTMLResponse, dependencies=protected)
    async def save_settings(request: Request) -> Any:
        try:
            raw = raw_config_from_form(
                await _urlencoded_form(request),
                runtime.config_manager.load_raw(),
            )
            result = runtime.config_manager.save(raw, runtime.config)
            await runtime.apply_config(result.config)
            message = "Settings saved and applied."
            if result.restart_required:
                message = "Settings saved. Restart the container for TLS or database path changes."
            return templates.TemplateResponse(
                request,
                "settings.html",
                settings_context(
                    runtime,
                    message=message,
                    restart_required=result.restart_required,
                ),
            )
        except ConfigError as exc:
            return templates.TemplateResponse(
                request,
                "settings.html",
                settings_context(runtime, error=str(exc)),
                status_code=400,
            )
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "settings.html",
                settings_context(runtime, error=str(exc)),
                status_code=400,
            )

    @router.get("/drift", response_class=HTMLResponse, dependencies=protected)
    async def drift_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request,
            "drift.html",
            {
                "dry_run": runtime.config.dry_run,
                "drift": format_drift_for_display(runtime.storage.current_drift()),
            },
        )

    @router.get("/history", response_class=HTMLResponse, dependencies=protected)
    async def history_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request,
            "history.html",
            {"dry_run": runtime.config.dry_run, "runs": runtime.storage.recent_runs()},
        )

    @router.get("/api/status", dependencies=protected)
    async def api_status() -> dict[str, Any]:
        return {
            "dry_run": runtime.config.dry_run,
            "followers": runtime.storage.latest_run_per_follower(),
            "host_health": configured_host_health(runtime.config, runtime.storage),
        }

    @router.get("/api/runs", dependencies=protected)
    async def api_runs() -> list[dict[str, Any]]:
        return runtime.storage.recent_runs()

    @router.get("/api/drift", dependencies=protected)
    async def api_drift() -> list[dict[str, Any]]:
        return runtime.storage.current_drift()

    @router.post("/api/sync", dependencies=protected)
    async def api_sync() -> dict[str, Any]:
        return {"run_ids": await runtime.scheduler.trigger_now()}

    return router
