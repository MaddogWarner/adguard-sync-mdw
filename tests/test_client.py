from __future__ import annotations

import httpx
import pytest
import respx

from app.adguard.client import AdGuardApiError, AdGuardClient


@pytest.mark.asyncio
async def test_snapshot_assembles_host_snapshot(host_config):
    with respx.mock(base_url="http://adguard.local") as router:
        router.get("/control/status").mock(return_value=httpx.Response(200, json={"version": "v1"}))
        router.get("/control/filtering/status").mock(
            return_value=httpx.Response(
                200,
                json={
                    "filters": [{"url": "https://block.example/list.txt", "name": "Block"}],
                    "whitelist_filters": [
                        {"url": "https://allow.example/list.txt", "name": "Allow"}
                    ],
                    "user_rules": ["||example.com^"],
                    "interval": 24,
                },
            )
        )
        router.get("/control/rewrite/list").mock(
            return_value=httpx.Response(200, json=[{"domain": "a.test", "answer": "1.1.1.1"}])
        )
        router.get("/control/dns_info").mock(
            return_value=httpx.Response(
                200,
                json={
                    "upstream_dns": ["1.1.1.1"],
                    "bootstrap_dns": ["9.9.9.9"],
                    "fallback_dns": [],
                    "upstream_mode": "parallel",
                },
            )
        )

        client = AdGuardClient(host_config)
        result = await client.snapshot()
        await client.aclose()

    assert result.host == "primary"
    assert result.version == "v1"
    assert result.blocklists[0].url == "https://block.example/list.txt"
    assert result.allowlists[0].url == "https://allow.example/list.txt"
    assert result.rewrites[0].domain == "a.test"


@pytest.mark.asyncio
async def test_write_methods_use_expected_paths_and_bodies(host_config):
    with respx.mock(base_url="http://adguard.local") as router:
        add = router.post("/control/filtering/add_url").mock(return_value=httpx.Response(200))
        remove = router.post("/control/filtering/remove_url").mock(return_value=httpx.Response(200))
        set_url = router.post("/control/filtering/set_url").mock(return_value=httpx.Response(200))
        rules = router.post("/control/filtering/set_rules").mock(return_value=httpx.Response(200))
        dns = router.post("/control/dns_config").mock(return_value=httpx.Response(200))
        refresh = router.post("/control/filtering/refresh").mock(return_value=httpx.Response(200))

        client = AdGuardClient(host_config)
        await client.add_filter("https://example/list.txt", "Example", whitelist=False)
        await client.remove_filter("https://example/list.txt", whitelist=True)
        await client.set_filter("https://example/list.txt", "Example", False, whitelist=False)
        await client.set_user_rules(["||example.com^"])
        await client.set_dns_config(
            client_module_upstream(["1.1.1.1"], ["9.9.9.9"], [], "parallel")
        )
        await client.refresh_filters(whitelist=True)
        await client.aclose()

    assert add.calls.last.request.content == (
        b'{"url":"https://example/list.txt","name":"Example","whitelist":false}'
    )
    assert remove.calls.last.request.content == (
        b'{"url":"https://example/list.txt","whitelist":true}'
    )
    assert b'"enabled":false' in set_url.calls.last.request.content
    assert rules.calls.last.request.content == b'{"rules":["||example.com^"]}'
    assert dns.calls.last.request.content == (
        b'{"upstream_dns":["1.1.1.1"],"bootstrap_dns":["9.9.9.9"],'
        b'"fallback_dns":[],"upstream_mode":"parallel"}'
    )
    assert refresh.calls.last.request.content == b'{"whitelist":true}'


def client_module_upstream(upstream_dns, bootstrap_dns, fallback_dns, upstream_mode):
    from app.adguard.models import UpstreamDnsConfig

    return UpstreamDnsConfig(
        upstream_dns=upstream_dns,
        bootstrap_dns=bootstrap_dns,
        fallback_dns=fallback_dns,
        upstream_mode=upstream_mode,
    )


@pytest.mark.asyncio
async def test_snapshot_unreachable_returns_unreachable(host_config):
    with respx.mock(base_url="http://adguard.local") as router:
        router.get("/control/status").mock(side_effect=httpx.ConnectError("no route"))
        client = AdGuardClient(host_config)
        result = await client.snapshot()
        await client.aclose()

    assert result.reachable is False


@pytest.mark.asyncio
async def test_non_2xx_raises_typed_error(host_config):
    with respx.mock(base_url="http://adguard.local") as router:
        router.post("/control/filtering/add_url").mock(return_value=httpx.Response(500, text="bad"))
        client = AdGuardClient(host_config)
        with pytest.raises(AdGuardApiError):
            await client.add_filter("https://example/list.txt", "Example", whitelist=False)
        await client.aclose()
