from __future__ import annotations

from app.adguard.models import (
    BlockedServices,
    Filter,
    HostSnapshot,
    Rewrite,
    UpstreamDnsConfig,
)
from app.config import ScopeConfig
from app.sync.result import ChangeAction, Domain, DomainResult, DriftItem, Op


def _filter_value(item: Filter) -> dict[str, object]:
    return {"url": item.url, "name": item.name, "enabled": item.enabled}


def diff_filters(
    primary: list[Filter],
    follower: list[Filter],
    *,
    whitelist: bool,
    prune: bool,
) -> DomainResult:
    domain = Domain.ALLOWLISTS if whitelist else Domain.BLOCKLISTS
    result = DomainResult(domain)
    primary_by_url = {item.url: item for item in primary}
    follower_by_url = {item.url: item for item in follower}

    for url, primary_item in primary_by_url.items():
        follower_item = follower_by_url.get(url)
        if follower_item is None:
            result.actions.append(
                ChangeAction(
                    domain,
                    Op.ADD,
                    url,
                    {"url": url, "name": primary_item.name, "enabled": primary_item.enabled},
                )
            )
            result.drift.append(
                DriftItem(domain, "missing", url, _filter_value(primary_item), None)
            )
        elif (
            primary_item.name != follower_item.name or primary_item.enabled != follower_item.enabled
        ):
            result.actions.append(
                ChangeAction(
                    domain,
                    Op.UPDATE,
                    url,
                    {"url": url, "name": primary_item.name, "enabled": primary_item.enabled},
                )
            )
            result.drift.append(
                DriftItem(
                    domain,
                    "changed",
                    url,
                    _filter_value(primary_item),
                    _filter_value(follower_item),
                )
            )

    for url, follower_item in follower_by_url.items():
        if url in primary_by_url:
            continue
        result.drift.append(DriftItem(domain, "extra", url, None, _filter_value(follower_item)))
        if prune:
            result.actions.append(ChangeAction(domain, Op.REMOVE, url, {"url": url}))

    return result


def diff_user_rules(primary: list[str], follower: list[str]) -> DomainResult:
    result = DomainResult(Domain.USER_RULES)
    if primary != follower:
        result.actions.append(
            ChangeAction(Domain.USER_RULES, Op.REPLACE, "user_rules", {"rules": primary})
        )
        result.drift.append(
            DriftItem(Domain.USER_RULES, "changed", "user_rules", primary, follower)
        )
    return result


def _rewrite_key(item: Rewrite) -> tuple[str, str]:
    return (item.domain, item.answer)


def diff_rewrites(primary: list[Rewrite], follower: list[Rewrite], *, prune: bool) -> DomainResult:
    result = DomainResult(Domain.REWRITES)
    primary_by_key = {_rewrite_key(item): item for item in primary}
    follower_by_key = {_rewrite_key(item): item for item in follower}

    for key, primary_item in primary_by_key.items():
        target = " -> ".join(key)
        if key not in follower_by_key:
            detail = {"domain": primary_item.domain, "answer": primary_item.answer}
            result.actions.append(ChangeAction(Domain.REWRITES, Op.ADD, target, detail))
            result.drift.append(DriftItem(Domain.REWRITES, "missing", target, detail, None))

    for key, follower_item in follower_by_key.items():
        target = " -> ".join(key)
        if key in primary_by_key:
            continue
        detail = {"domain": follower_item.domain, "answer": follower_item.answer}
        result.drift.append(DriftItem(Domain.REWRITES, "extra", target, None, detail))
        if prune:
            result.actions.append(ChangeAction(Domain.REWRITES, Op.REMOVE, target, detail))

    return result


def _normalise_upstream(value: UpstreamDnsConfig) -> dict[str, object]:
    return {
        "upstream_dns": sorted(value.upstream_dns),
        "bootstrap_dns": sorted(value.bootstrap_dns),
        "fallback_dns": sorted(value.fallback_dns),
        "upstream_mode": value.upstream_mode,
    }


def diff_upstream(primary: UpstreamDnsConfig, follower: UpstreamDnsConfig) -> DomainResult:
    result = DomainResult(Domain.UPSTREAM_DNS)
    primary_value = _normalise_upstream(primary)
    follower_value = _normalise_upstream(follower)
    if primary_value != follower_value:
        result.actions.append(
            ChangeAction(Domain.UPSTREAM_DNS, Op.REPLACE, "upstream_dns", primary.model_dump())
        )
        result.drift.append(
            DriftItem(Domain.UPSTREAM_DNS, "changed", "upstream_dns", primary_value, follower_value)
        )
    return result


def _normalise_blocked_services(value: BlockedServices) -> dict[str, object]:
    return {"ids": sorted(value.ids), "schedule": value.schedule}


def diff_blocked_services(primary: BlockedServices, follower: BlockedServices) -> DomainResult:
    result = DomainResult(Domain.BLOCKED_SERVICES)
    primary_value = _normalise_blocked_services(primary)
    follower_value = _normalise_blocked_services(follower)
    if primary_value != follower_value:
        result.actions.append(
            ChangeAction(
                Domain.BLOCKED_SERVICES,
                Op.REPLACE,
                "blocked_services",
                {"ids": primary.ids, "schedule": primary.schedule},
            )
        )
        result.drift.append(
            DriftItem(
                Domain.BLOCKED_SERVICES,
                "changed",
                "blocked_services",
                primary_value,
                follower_value,
            )
        )
    return result


def diff_host(
    primary_snapshot: HostSnapshot,
    follower_snapshot: HostSnapshot,
    scope: ScopeConfig,
) -> list[DomainResult]:
    results: list[DomainResult] = []
    if scope.blocklists.enabled:
        results.append(
            diff_filters(
                primary_snapshot.blocklists,
                follower_snapshot.blocklists,
                whitelist=False,
                prune=scope.blocklists.prune,
            )
        )
    if scope.allowlists.enabled:
        results.append(
            diff_filters(
                primary_snapshot.allowlists,
                follower_snapshot.allowlists,
                whitelist=True,
                prune=scope.allowlists.prune,
            )
        )
    if scope.user_rules.enabled:
        results.append(diff_user_rules(primary_snapshot.user_rules, follower_snapshot.user_rules))
    if scope.rewrites.enabled:
        results.append(
            diff_rewrites(
                primary_snapshot.rewrites,
                follower_snapshot.rewrites,
                prune=scope.rewrites.prune,
            )
        )
    if scope.upstream_dns.enabled:
        results.append(diff_upstream(primary_snapshot.upstream, follower_snapshot.upstream))
    if (
        scope.blocked_services.enabled
        and primary_snapshot.blocked_services_supported
        and follower_snapshot.blocked_services_supported
    ):
        results.append(
            diff_blocked_services(
                primary_snapshot.blocked_services,
                follower_snapshot.blocked_services,
            )
        )
    return results
