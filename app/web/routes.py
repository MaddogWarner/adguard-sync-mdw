from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app.config import AppConfig
from app.storage import Storage

templates = Jinja2Templates(directory="app/web/templates")
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
            {
                "dry_run": config.dry_run,
                "runs": storage.latest_run_per_follower(),
            },
        )

    @router.get("/drift", response_class=HTMLResponse, dependencies=protected)
    async def drift_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request,
            "drift.html",
            {"dry_run": config.dry_run, "drift": storage.current_drift()},
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
        return {"dry_run": config.dry_run, "followers": storage.latest_run_per_follower()}

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
