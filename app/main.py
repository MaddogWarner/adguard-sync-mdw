from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.config_manager import ConfigManager
from app.logging_setup import setup_logging
from app.runtime import AppRuntime
from app.tls import resolve_tls
from app.web.routes import create_router


def build_app() -> FastAPI:
    config_manager = ConfigManager()
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
