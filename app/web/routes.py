from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import AppConfig
from app.storage import Storage

templates = Jinja2Templates(directory="app/web/templates")
templates.env.globals["app_version"] = __version__
security = HTTPBasic(auto_error=False)


def auth_dependency(config: AppConfig):
    async def verify(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
    ) -> None:
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


def create_router(
    *,
    config: AppConfig,
    storage: Storage,
    trigger_sync: Callable[[], Awaitable[list[int]]],
) -> APIRouter:
    router = APIRouter()
    protected = [Depends(auth_dependency(config))]

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/", response_class=HTMLResponse, dependencies=protected)
    async def status_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request,
            "status.html",
            status_context(config, storage),
        )

    @router.post("/sync-now", response_class=HTMLResponse, dependencies=protected)
    async def sync_now(request: Request) -> Any:
        run_ids = await trigger_sync()
        return templates.TemplateResponse(
            request,
            "status_content.html",
            status_context(config, storage, _sync_message(run_ids)),
        )

    @router.get("/drift", response_class=HTMLResponse, dependencies=protected)
    async def drift_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request,
            "drift.html",
            {
                "dry_run": config.dry_run,
                "drift": format_drift_for_display(storage.current_drift()),
            },
        )

    @router.get("/history", response_class=HTMLResponse, dependencies=protected)
    async def history_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request,
            "history.html",
            {"dry_run": config.dry_run, "runs": storage.recent_runs()},
        )

    @router.get("/api/status", dependencies=protected)
    async def api_status() -> dict[str, Any]:
        return {
            "dry_run": config.dry_run,
            "followers": storage.latest_run_per_follower(),
            "host_health": configured_host_health(config, storage),
        }

    @router.get("/api/runs", dependencies=protected)
    async def api_runs() -> list[dict[str, Any]]:
        return storage.recent_runs()

    @router.get("/api/drift", dependencies=protected)
    async def api_drift() -> list[dict[str, Any]]:
        return storage.current_drift()

    @router.post("/api/sync", dependencies=protected)
    async def api_sync() -> dict[str, Any]:
        return {"run_ids": await trigger_sync()}

    return router
