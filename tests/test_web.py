from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig, HostConfig
from app.storage import Storage
from app.web.routes import create_router


def test_healthz_and_status_page_render(tmp_path):
    config = AppConfig(
        interval_minutes=5,
        dry_run=True,
        database_path=str(tmp_path / "adguard-sync.db"),
        primary=HostConfig(
            name="primary", url="http://primary.local", username="admin", password="x"
        ),
        followers=[
            HostConfig(
                name="follower-a", url="http://follower-a.local", username="admin", password="x"
            )
        ],
    )
    storage = Storage(config.database_path)
    storage.init_db()
    storage.record_run(follower="follower-a", status="in_sync")

    async def trigger_sync() -> list[int]:
        return [1]

    app = FastAPI()
    app.include_router(create_router(config=config, storage=storage, trigger_sync=trigger_sync))
    client = TestClient(app)

    assert client.get("/healthz").json() == {"status": "ok"}
    response = client.get("/")
    assert response.status_code == 200
    assert "DRY RUN" in response.text
    assert "follower-a" in response.text
