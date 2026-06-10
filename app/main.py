from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.adguard.client import AdGuardClient
from app.config import load_config
from app.logging_setup import setup_logging
from app.scheduler import SyncScheduler
from app.storage import Storage
from app.sync.engine import SyncEngine
from app.tls import resolve_tls
from app.web.routes import create_router


def build_app() -> FastAPI:
    config = load_config()
    setup_logging(config.log_level)
    storage = Storage(config.database_path)
    storage.init_db()
    storage.purge_history_older_than(config.history_retention_days)
    clients = {
        config.primary.name: AdGuardClient(config.primary),
        **{follower.name: AdGuardClient(follower) for follower in config.followers},
    }
    engine = SyncEngine(config, storage, clients)
    scheduler = SyncScheduler(
        engine,
        config.interval_minutes,
        storage,
        config.history_retention_days,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown()
            for client in clients.values():
                await client.aclose()

    app = FastAPI(title="AdGuard Sync", lifespan=lifespan)
    app.state.config = config
    app.state.storage = storage
    app.state.scheduler = scheduler
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
    app.include_router(
        create_router(config=config, storage=storage, trigger_sync=scheduler.trigger_now)
    )
    return app


app = build_app()


def main() -> None:
    tls = resolve_tls(app.state.config)
    ssl_kwargs = {"ssl_certfile": tls.cert_file, "ssl_keyfile": tls.key_file} if tls else {}
    uvicorn.run(app, host="0.0.0.0", port=8080, **ssl_kwargs)


if __name__ == "__main__":
    main()
