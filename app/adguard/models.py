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
    rewrites: list[Rewrite] = Field(default_factory=list)
    upstream: UpstreamDnsConfig = Field(default_factory=UpstreamDnsConfig)
    blocked_services: BlockedServices = Field(default_factory=BlockedServices)
    blocked_services_supported: bool = True
    version: str | None = None
    reachable: bool = True
    error: str | None = None
