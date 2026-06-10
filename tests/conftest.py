from __future__ import annotations

import pytest

from app.adguard.models import Filter, HostSnapshot, Rewrite, UpstreamDnsConfig
from app.config import HostConfig


@pytest.fixture
def host_config() -> HostConfig:
    return HostConfig(
        name="primary",
        url="http://adguard.local",
        username="admin",
        password="secret",
        verify_ssl=True,
    )


def snapshot(
    *,
    host: str = "host",
    blocklists: list[Filter] | None = None,
    allowlists: list[Filter] | None = None,
    user_rules: list[str] | None = None,
    rewrites: list[Rewrite] | None = None,
    upstream: UpstreamDnsConfig | None = None,
    reachable: bool = True,
) -> HostSnapshot:
    return HostSnapshot(
        host=host,
        blocklists=blocklists or [],
        allowlists=allowlists or [],
        user_rules=user_rules or [],
        rewrites=rewrites or [],
        upstream=upstream or UpstreamDnsConfig(),
        reachable=reachable,
    )
