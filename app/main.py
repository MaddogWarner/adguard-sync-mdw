from __future__ import annotations

import contextlib
import logging
import os
import pwd
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.config_manager import ConfigManager
from app.logging_setup import setup_logging
from app.runtime import AppRuntime
from app.tls import resolve_tls
from app.web.routes import create_router

_logger = logging.getLogger(__name__)


def _drop_privileges_if_root() -> None:
    if os.getuid() != 0:
        return
    try:
        pw = pwd.getpwnam("adguard-sync")
    except KeyError:
        return
    for path in ("/config", "/data"):
        with contextlib.suppress(OSError):
            os.chown(path, pw.pw_uid, pw.pw_gid)
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)


def _seed_config_if_missing(config_path: Path) -> None:
    if config_path.exists():
        return
    example = Path(__file__).parent.parent / "config.example.yaml"
    if not example.exists():
        return
    _logger.info(
        "config.yaml not found — seeding from config.example.yaml. "
        "Open the Settings page to configure your AdGuard hosts."
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(example, config_path)


def build_app() -> FastAPI:
    _drop_privileges_if_root()
    config_manager = ConfigManager()
    _seed_config_if_missing(config_manager.path)
    config = load_config(config_manager.path)
    setup_logging(config.log_level)
    runtime = AppRuntime.build(config, config_manager)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        runtime.scheduler.start()
        try:
            yield
        finally:
            await runtime.shutdown()

    app = FastAPI(title="AdGuard Sync", lifespan=lifespan)
    app.state.runtime = runtime
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
    app.include_router(create_router(runtime=runtime))
    return app


app = build_app()


def main() -> None:
    tls = resolve_tls(app.state.runtime.config)
    ssl_kwargs = {"ssl_certfile": tls.cert_file, "ssl_keyfile": tls.key_file} if tls else {}
    uvicorn.run(app, host="0.0.0.0", port=8080, **ssl_kwargs)


if __name__ == "__main__":
    main()
