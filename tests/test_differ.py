from __future__ import annotations

from app.adguard.models import BlockedServices, Filter, Rewrite, UpstreamDnsConfig
from app.config import ScopeConfig, ScopeItem
from app.sync.differ import (
    diff_blocked_services,
    diff_filters,
    diff_host,
    diff_rewrites,
    diff_upstream,
    diff_user_rules,
)
from app.sync.result import Domain, Op
from tests.conftest import snapshot


def test_diff_filters_add_update_prune_and_drift():
    result = diff_filters(
        [
            Filter(url="https://a.test/list.txt", name="A", enabled=True),
            Filter(url="https://b.test/list.txt", name="B", enabled=False),
        ],
        [
            Filter(url="https://b.test/list.txt", name="Old B", enabled=True),
            Filter(url="https://extra.test/list.txt", name="Extra", enabled=True),
        ],
        whitelist=False,
        prune=True,
    )

    assert [action.op for action in result.actions] == [Op.ADD, Op.UPDATE, Op.REMOVE]
    assert len(result.drift) == 3


def test_diff_filters_prune_off_retains_extra_but_reports_drift():
    result = diff_filters(
        [],
        [Filter(url="https://extra.test/list.txt", name="Extra")],
        whitelist=True,
        prune=False,
    )

    assert result.actions == []
    assert result.drift[0].kind == "extra"
    assert result.domain == Domain.ALLOWLISTS


def test_diff_filters_noop():
    item = Filter(url="https://a.test/list.txt", name="A", enabled=True)

    result = diff_filters([item], [item], whitelist=False, prune=True)

    assert result.actions == []
    assert result.drift == []


def test_diff_user_rules_replace_when_order_differs():
    result = diff_user_rules(["a", "b"], ["b", "a"])

    assert result.actions[0].op == Op.REPLACE
    assert result.drift[0].target == "user_rules"


def test_diff_user_rules_noop():
    result = diff_user_rules(["a"], ["a"])

    assert result.actions == []
    assert result.drift == []


def test_diff_rewrites_add_remove_and_prune_off():
    add_remove = diff_rewrites(
        [Rewrite(domain="a.test", answer="1.1.1.1")],
        [Rewrite(domain="b.test", answer="2.2.2.2")],
        prune=True,
    )
    keep_extra = diff_rewrites([], [Rewrite(domain="b.test", answer="2.2.2.2")], prune=False)

    assert [action.op for action in add_remove.actions] == [Op.ADD, Op.REMOVE]
    assert keep_extra.actions == []
    assert keep_extra.drift[0].kind == "extra"


def test_diff_rewrites_noop():
    rewrite = Rewrite(domain="a.test", answer="1.1.1.1")

    result = diff_rewrites([rewrite], [rewrite], prune=True)

    assert result.actions == []
    assert result.drift == []


def test_diff_upstream_replace_with_normalised_lists():
    primary = UpstreamDnsConfig(
        upstream_dns=["1.1.1.1", "8.8.8.8"],
        bootstrap_dns=["9.9.9.9"],
        fallback_dns=[],
        upstream_mode="parallel",
    )
    same = UpstreamDnsConfig(
        upstream_dns=["8.8.8.8", "1.1.1.1"],
        bootstrap_dns=["9.9.9.9"],
        fallback_dns=[],
        upstream_mode="parallel",
    )
    different = UpstreamDnsConfig(upstream_dns=["1.1.1.1"], upstream_mode="load_balance")

    assert diff_upstream(primary, same).actions == []
    assert diff_upstream(primary, different).actions[0].op == Op.REPLACE


def test_diff_blocked_services_replace_when_ids_or_schedule_differ():
    primary = BlockedServices(ids=["facebook", "tiktok"], schedule={"time_zone": "Local"})
    changed_ids = BlockedServices(ids=["facebook"], schedule={"time_zone": "Local"})
    changed_schedule = BlockedServices(ids=["facebook", "tiktok"], schedule=None)

    ids_result = diff_blocked_services(primary, changed_ids)
    schedule_result = diff_blocked_services(primary, changed_schedule)

    assert ids_result.actions[0].op == Op.REPLACE
    assert ids_result.actions[0].detail == {
        "ids": ["facebook", "tiktok"],
        "schedule": {"time_zone": "Local"},
    }
    assert ids_result.drift[0].target == "blocked_services"
    assert schedule_result.actions[0].op == Op.REPLACE


def test_diff_blocked_services_noop_ignores_id_order():
    primary = BlockedServices(ids=["facebook", "tiktok"], schedule={"time_zone": "Local"})
    follower = BlockedServices(ids=["tiktok", "facebook"], schedule={"time_zone": "Local"})

    result = diff_blocked_services(primary, follower)

    assert result.actions == []
    assert result.drift == []


def test_diff_host_honours_scope():
    primary = snapshot(blocklists=[Filter(url="https://a.test/list.txt", name="A")])
    follower = snapshot()
    scope = ScopeConfig(
        blocklists=ScopeItem(enabled=False, prune=True),
        allowlists=ScopeItem(enabled=False, prune=True),
        user_rules=ScopeItem(enabled=False, prune=False),
        rewrites=ScopeItem(enabled=False, prune=True),
        upstream_dns=ScopeItem(enabled=False, prune=False),
        blocked_services=ScopeItem(enabled=False, prune=False),
    )

    assert diff_host(primary, follower, scope) == []
