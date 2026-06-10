from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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


class FilteringStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    filters: list[Filter] = Field(default_factory=list)
    whitelist_filters: list[Filter] = Field(default_factory=list)
    user_rules: list[str] = Field(default_factory=list)
    interval: int | None = None


class HostSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: str
    blocklists: list[Filter] = Field(default_factory=list)
    allowlists: list[Filter] = Field(default_factory=list)
    user_rules: list[str] = Field(default_factory=list)
    rewrites: list[Rewrite] = Field(default_factory=list)
    upstream: UpstreamDnsConfig = Field(default_factory=UpstreamDnsConfig)
    version: str | None = None
    reachable: bool = True
