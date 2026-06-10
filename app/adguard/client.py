from __future__ import annotations

import logging
from typing import Any

import httpx

from app.adguard.models import (
    BlockedServices,
    FilteringStatus,
    HostSnapshot,
    Rewrite,
    UpstreamDnsConfig,
)
from app.config import HostConfig

logger = logging.getLogger(__name__)


class AdGuardApiError(RuntimeError):
    def __init__(self, host: str, method: str, path: str, status_code: int, body: str) -> None:
        super().__init__(f"{host} {method} {path} failed with HTTP {status_code}")
        self.host = host
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body


class AdGuardClient:
    def __init__(self, config: HostConfig, *, timeout: float = 15.0) -> None:
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.url,
            auth=(config.username, config.password.get_secret_value()),
            verify=config.verify_ssl,
            timeout=timeout,
        )

    async def __aenter__(self) -> AdGuardClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> Any:
        response = await self._client.get(path)
        self._raise_for_status("GET", path, response)
        return response.json()

    async def _post(self, path: str, payload: dict[str, Any] | None = None) -> None:
        response = await self._client.post(path, json=payload or {})
        self._raise_for_status("POST", path, response)

    async def _put(self, path: str, payload: dict[str, Any] | None = None) -> None:
        response = await self._client.put(path, json=payload or {})
        self._raise_for_status("PUT", path, response)

    def _raise_for_status(self, method: str, path: str, response: httpx.Response) -> None:
        if response.is_success:
            return
        raise AdGuardApiError(
            self.config.name,
            method,
            path,
            response.status_code,
            response.text,
        )

    async def get_status(self) -> dict[str, Any]:
        return await self._get("/control/status")

    async def get_filtering_status(self) -> FilteringStatus:
        return FilteringStatus.model_validate(await self._get("/control/filtering/status"))

    async def get_rewrites(self) -> list[Rewrite]:
        return [Rewrite.model_validate(item) for item in await self._get("/control/rewrite/list")]

    async def get_dns_info(self) -> UpstreamDnsConfig:
        return UpstreamDnsConfig.model_validate(await self._get("/control/dns_info"))

    async def get_blocked_services(self) -> BlockedServices:
        return BlockedServices.model_validate(await self._get("/control/blocked_services/get"))

    async def snapshot(self) -> HostSnapshot:
        try:
            status = await self.get_status()
            filtering = await self.get_filtering_status()
            rewrites = await self.get_rewrites()
            upstream = await self.get_dns_info()
        except (httpx.HTTPError, AdGuardApiError, ValueError) as exc:
            logger.warning(
                "adguard_host_unreachable",
                extra={"host": self.config.name, "error": str(exc)},
            )
            return HostSnapshot(host=self.config.name, reachable=False, error=str(exc))

        # Blocked services uses a newer endpoint that older AdGuard versions lack.
        # A failure here must not mark the whole host unreachable, so it is isolated.
        try:
            blocked_services = await self.get_blocked_services()
            blocked_services_supported = True
        except (httpx.HTTPError, AdGuardApiError, ValueError) as exc:
            logger.info(
                "blocked_services_unavailable",
                extra={"host": self.config.name, "error": str(exc)},
            )
            blocked_services = BlockedServices()
            blocked_services_supported = False

        return HostSnapshot(
            host=self.config.name,
            blocklists=filtering.filters,
            allowlists=filtering.whitelist_filters,
            user_rules=filtering.user_rules,
            rewrites=rewrites,
            upstream=upstream,
            blocked_services=blocked_services,
            blocked_services_supported=blocked_services_supported,
            version=status.get("version"),
            reachable=True,
        )

    async def add_filter(self, url: str, name: str, *, whitelist: bool) -> None:
        await self._post(
            "/control/filtering/add_url",
            {"url": url, "name": name, "whitelist": whitelist},
        )

    async def remove_filter(self, url: str, *, whitelist: bool) -> None:
        await self._post("/control/filtering/remove_url", {"url": url, "whitelist": whitelist})

    async def set_filter(self, url: str, name: str, enabled: bool, *, whitelist: bool) -> None:
        await self._post(
            "/control/filtering/set_url",
            {
                "url": url,
                "whitelist": whitelist,
                "data": {"name": name, "url": url, "enabled": enabled},
            },
        )

    async def set_user_rules(self, rules: list[str]) -> None:
        await self._post("/control/filtering/set_rules", {"rules": rules})

    async def set_filtering_interval(self, interval: int) -> None:
        await self._post("/control/filtering/config", {"interval": interval})

    async def add_rewrite(self, domain: str, answer: str) -> None:
        await self._post("/control/rewrite/add", {"domain": domain, "answer": answer})

    async def delete_rewrite(self, domain: str, answer: str) -> None:
        await self._post("/control/rewrite/delete", {"domain": domain, "answer": answer})

    async def set_dns_config(self, cfg: UpstreamDnsConfig) -> None:
        await self._post(
            "/control/dns_config",
            cfg.model_dump(
                include={"upstream_dns", "bootstrap_dns", "fallback_dns", "upstream_mode"}
            ),
        )

    async def refresh_filters(self, *, whitelist: bool) -> None:
        await self._post("/control/filtering/refresh", {"whitelist": whitelist})

    async def set_blocked_services(self, services: BlockedServices) -> None:
        await self._put(
            "/control/blocked_services/update",
            {"ids": services.ids, "schedule": services.schedule},
        )
