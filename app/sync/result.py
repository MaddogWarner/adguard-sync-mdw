from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Domain(StrEnum):
    BLOCKLISTS = "blocklists"
    ALLOWLISTS = "allowlists"
    USER_RULES = "user_rules"
    REWRITES = "rewrites"
    UPSTREAM_DNS = "upstream_dns"
    BLOCKED_SERVICES = "blocked_services"


class Op(StrEnum):
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"
    REPLACE = "replace"


@dataclass(frozen=True)
class ChangeAction:
    domain: Domain
    op: Op
    target: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class DriftItem:
    domain: Domain
    kind: str
    target: str
    primary_value: Any
    follower_value: Any


@dataclass
class DomainResult:
    domain: Domain
    actions: list[ChangeAction] = field(default_factory=list)
    drift: list[DriftItem] = field(default_factory=list)
