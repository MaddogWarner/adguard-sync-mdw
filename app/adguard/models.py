from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Filter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    name: str = ""
    enabled: bool = True
    id: int | None = None


class Rewrite(BaseModel):
    model_config = ConfigDict(extra="ignore")

    domain: str
    answer: str
    # None: the host's AdGuard version (< v0.107.68) does not report an enabled flag.
    enabled: bool | None = None


class UpstreamDnsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    upstream_dns: list[str] = Field(default_factory=list)
    bootstrap_dns: list[str] = Field(default_factory=list)
    fallback_dns: list[str] = Field(default_factory=list)
    upstream_mode: str = ""

    @field_validator("upstream_dns", "bootstrap_dns", "fallback_dns", mode="before")
    @classmethod
    def empty_list_when_null(cls, value: Any) -> Any:
        return [] if value is None else value


# Settings returned by /control/dns_info that dns_settings sync manages. Each field is
# optional: None means the host's AdGuard version does not report it, and the differ
# skips that field rather than pushing a value the follower cannot accept.
DNS_SETTINGS_FIELDS = (
    "ratelimit",
    "ratelimit_subnet_len_ipv4",
    "ratelimit_subnet_len_ipv6",
    "ratelimit_whitelist",
    "dnssec_enabled",
    "cache_enabled",
    "cache_size",
    "cache_ttl_min",
    "cache_ttl_max",
    "cache_optimistic",
)


class DnsSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ratelimit: int | None = None
    ratelimit_subnet_len_ipv4: int | None = None
    ratelimit_subnet_len_ipv6: int | None = None
    ratelimit_whitelist: list[str] | None = None
    dnssec_enabled: bool | None = None
    cache_enabled: bool | None = None
    cache_size: int | None = None
    cache_ttl_min: int | None = None
    cache_ttl_max: int | None = None
    cache_optimistic: bool | None = None


class QuerylogConfig(BaseModel):
    """Body of GET /control/querylog/config; interval is in milliseconds."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    interval: int = 0
    anonymize_client_ip: bool = False
    ignored: list[str] = Field(default_factory=list)
    ignored_enabled: bool | None = None

    @field_validator("ignored", mode="before")
    @classmethod
    def empty_list_when_null(cls, value: Any) -> Any:
        return [] if value is None else value


class BlockedServices(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ids: list[str] = Field(default_factory=list)
    schedule: dict[str, Any] | None = None

    @field_validator("ids", mode="before")
    @classmethod
    def empty_list_when_null(cls, value: Any) -> Any:
        return [] if value is None else value


class FilteringStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    filters: list[Filter] = Field(default_factory=list)
    whitelist_filters: list[Filter] = Field(default_factory=list)
    user_rules: list[str] = Field(default_factory=list)
    interval: int | None = None
    enabled: bool | None = None

    @field_validator("filters", "whitelist_filters", "user_rules", mode="before")
    @classmethod
    def empty_list_when_null(cls, value: Any) -> Any:
        return [] if value is None else value


class HostSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: str
    blocklists: list[Filter] = Field(default_factory=list)
    allowlists: list[Filter] = Field(default_factory=list)
    user_rules: list[str] = Field(default_factory=list)
    filter_interval: int | None = None
    filtering_enabled: bool | None = None
    rewrites: list[Rewrite] = Field(default_factory=list)
    upstream: UpstreamDnsConfig = Field(default_factory=UpstreamDnsConfig)
    blocked_services: BlockedServices = Field(default_factory=BlockedServices)
    blocked_services_supported: bool = True
    dns_settings: DnsSettings = Field(default_factory=DnsSettings)
    querylog: QuerylogConfig = Field(default_factory=QuerylogConfig)
    querylog_supported: bool = True
    version: str | None = None
    reachable: bool = True
    error: str | None = None
