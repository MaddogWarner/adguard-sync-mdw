from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from app.config import NotificationsConfig
from app.notify import Alert, Notifier
from app.storage import Storage
from app.sync.engine import SyncEngine
from tests.test_engine import close_engine, make_config, mock_snapshot

HOOK = "https://hooks.example/token-abc"
ALERT = Alert("host_down", "follower-a", "Follower follower-a is unreachable: down", "problem")


@pytest.mark.asyncio
async def test_json_webhook_payload():
    notifier = Notifier(NotificationsConfig(webhook_url=HOOK))
    with respx.mock as router:
        route = router.post(HOOK).mock(return_value=httpx.Response(200))
        await notifier.send(ALERT)

    body = json.loads(route.calls[0].request.content)
    assert body["event"] == "host_down"
    assert body["message"] in body["text"] == body["content"]


@pytest.mark.asyncio
async def test_ntfy_format_sends_plain_text_with_headers():
    notifier = Notifier(NotificationsConfig(webhook_url=HOOK, format="ntfy"))
    with respx.mock as router:
        route = router.post(HOOK).mock(return_value=httpx.Response(200))
        await notifier.send(ALERT)

    request = route.calls[0].request
    assert request.content.decode() == ALERT.message
    assert request.headers["Priority"] == "high"


@pytest.mark.asyncio
async def test_delivery_failure_is_swallowed_and_url_not_logged(caplog):
    notifier = Notifier(NotificationsConfig(webhook_url=HOOK))
    with respx.mock as router:
        router.post(HOOK).mock(side_effect=httpx.ConnectError("boom"))
        await notifier.send(ALERT)

    assert "token-abc" not in caplog.text


@pytest.mark.asyncio
async def test_disabled_notifier_does_nothing():
    notifier = Notifier(NotificationsConfig())
    assert not notifier.enabled
    await notifier.send(ALERT)


def test_webhook_url_validation():
    assert NotificationsConfig(webhook_url="  ").webhook_url is None
    with pytest.raises(ValidationError):
        NotificationsConfig(webhook_url="ftp://x")


@pytest.mark.asyncio
async def test_engine_alerts_only_on_state_change(tmp_path):
    config = make_config(tmp_path)
    storage = Storage(config.database_path)
    storage.init_db()
    from app.adguard.client import AdGuardClient

    clients = {
        config.primary.name: AdGuardClient(config.primary),
        "follower-a": AdGuardClient(config.followers[0]),
    }
    engine = SyncEngine(config, storage, clients, Notifier(NotificationsConfig(webhook_url=HOOK)))
    with respx.mock as router:
        hook = router.post(HOOK).mock(return_value=httpx.Response(200))
        mock_snapshot(router, "http://primary.local")
        router.get("http://follower-a.local/control/status").mock(
            side_effect=httpx.ConnectError("down")
        )
        await engine.run_once()
        await engine.run_once()  # still down: no repeat alert
        assert hook.call_count == 1
        assert json.loads(hook.calls[0].request.content)["event"] == "host_down"

    with respx.mock as router:
        hook = router.post(HOOK).mock(return_value=httpx.Response(200))
        mock_snapshot(router, "http://primary.local")
        mock_snapshot(router, "http://follower-a.local")
        await engine.run_once()
        assert [json.loads(c.request.content)["event"] for c in hook.calls] == ["host_up"]
    await close_engine(engine)
