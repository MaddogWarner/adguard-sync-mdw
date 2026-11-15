from __future__ import annotations

import asyncio
import logging

from app.adguard.client import AdGuardClient
from app.adguard.models import (
    BlockedServices,
    HostSnapshot,
    QuerylogConfig,
    UpstreamDnsConfig,
)
from app.config import AppConfig
from app.notify import Alert, Notifier
from app.storage import Storage, utc_now
from app.sync.differ import diff_host
from app.sync.result import ChangeAction, Domain, DriftItem, Op

logger = logging.getLogger(__name__)

# Removals smaller than this are never blocked by the prune guard (small lists are noisy).
PRUNE_GUARD_MIN_REMOVALS = 3
MAX_CONCURRENT_FOLLOWERS = 3
OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_DRY_RUN = "skipped(dry_run)"
OUTCOME_PRUNE_GUARD = "skipped(prune_guard)"


class SyncEngine:
    def __init__(
        self,
        config: AppConfig,
        storage: Storage,
        clients: dict[str, AdGuardClient],
        notifier: Notifier | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.clients = clients
        self.notifier = notifier

    async def run_once(self) -> list[int]:
        primary = await self.clients[self.config.primary.name].snapshot()
        await self._record_host_health(primary, role="primary", url=self.config.primary.url)
        if not primary.reachable:
            return [
                self.storage.record_run(
                    follower=follower.name,
                    status="failed",
                    error="primary unreachable",
                )
                for follower in self.config.followers
            ]

        # Followers are independent, so sync a few at a time; gather keeps config order.
        limit = asyncio.Semaphore(MAX_CONCURRENT_FOLLOWERS)

        async def run_follower(name: str) -> int:
            async with limit:
                previous = self.storage.latest_run_status(name)
                try:
                    return await self.sync_follower(primary, name)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("sync_follower_failed", extra={"follower": name})
                    run_id = self.storage.record_run(follower=name, status="failed", error=str(exc))
                    await self._alert_run(name, previous, "failed", str(exc))
                    return run_id

        return list(await asyncio.gather(*(run_follower(f.name) for f in self.config.followers)))

    async def sync_follower(self, primary_snapshot: HostSnapshot, follower_name: str) -> int:
        started_at = utc_now()
        previous_status = self.storage.latest_run_status(follower_name)
        follower_snapshot = await self.clients[follower_name].snapshot()
        follower_config = next(
            follower for follower in self.config.followers if follower.name == follower_name
        )
        await self._record_host_health(
            follower_snapshot,
            role="follower",
            url=follower_config.url,
        )
        if not follower_snapshot.reachable:
            return self.storage.record_run(
                follower=follower_name,
                status="failed",
                started_at=started_at,
                error="follower unreachable",
            )

        results = diff_host(primary_snapshot, follower_snapshot, self.config.scope)
        actions = [action for result in results for action in result.actions]
        drift = [item for result in results for item in result.drift]
        actions, blocked, guard_errors = self._apply_prune_guard(actions, follower_snapshot)

        outcomes: list[tuple[ChangeAction, str]] = [
            (action, OUTCOME_PRUNE_GUARD) for action in blocked
        ]
        failures: list[str] = []
        if self.config.dry_run:
            outcomes += [(action, OUTCOME_DRY_RUN) for action in actions]
        elif actions:
            applied, failures = await self._apply_actions(follower_name, actions)
            outcomes += applied

        status = self._status(actions, drift)
        errors = [*guard_errors, *failures]
        if (failures or guard_errors) and not self.config.dry_run:
            status = "partial"
        error = "; ".join(errors) if errors else None

        counts = self._counts(
            [a for a, outcome in outcomes if outcome in (OUTCOME_SUCCESS, OUTCOME_DRY_RUN)]
        )
        run_id = self.storage.record_run(
            follower=follower_name,
            status=status,
            started_at=started_at,
            added=counts["added"],
            updated=counts["updated"],
            removed=counts["removed"],
            error=error,
        )
        for outcome in dict.fromkeys(outcome for _, outcome in outcomes):
            self.storage.record_changes(
                run_id,
                [action for action, result in outcomes if result == outcome],
                outcome=outcome,
            )
        self.storage.record_drift(run_id, follower_name, drift)
        await self._alert_run(follower_name, previous_status, status, error)
        if actions and not self.config.dry_run:
            self.storage.record_snapshot(
                run_id,
                follower_name,
                follower_snapshot.model_dump(exclude={"host", "reachable", "error"}),
            )
        return run_id

    def _apply_prune_guard(
        self, actions: list[ChangeAction], follower: HostSnapshot
    ) -> tuple[list[ChangeAction], list[ChangeAction], list[str]]:
        """Drop a domain's removals when they would delete too large a share of the follower.

        Guards against a primary that returns an empty or truncated list (API glitch,
        partial restore) wiping every follower. Returns (kept, blocked, error messages).
        """
        totals = {
            Domain.BLOCKLISTS: len(follower.blocklists),
            Domain.ALLOWLISTS: len(follower.allowlists),
            Domain.REWRITES: len(follower.rewrites),
        }
        limit = self.config.max_prune_percent
        blocked_domains: set[Domain] = set()
        errors: list[str] = []
        for domain, total in totals.items():
            removals = sum(1 for a in actions if a.domain == domain and a.op == Op.REMOVE)
            if removals >= PRUNE_GUARD_MIN_REMOVALS and removals * 100 > limit * total:
                blocked_domains.add(domain)
                errors.append(
                    f"prune guard: skipped {removals} {domain.value} removals "
                    f"(follower has {total}, limit {limit}%)"
                )
        kept = [a for a in actions if not (a.op == Op.REMOVE and a.domain in blocked_domains)]
        blocked = [a for a in actions if a.op == Op.REMOVE and a.domain in blocked_domains]
        return kept, blocked, errors

    def _status(self, actions: list[ChangeAction], drift: list[DriftItem]) -> str:
        if not drift:
            return "in_sync"
        if self.config.dry_run:
            return "drift_detected"
        if actions:
            return "drift_corrected"
        return "in_sync"

    def _counts(self, actions: list[ChangeAction]) -> dict[str, int]:
        return {
            "added": sum(1 for action in actions if action.op == Op.ADD),
            "updated": sum(1 for action in actions if action.op in {Op.UPDATE, Op.REPLACE}),
            "removed": sum(1 for action in actions if action.op == Op.REMOVE),
        }

    async def _alert(self, alert: Alert) -> None:
        if self.notifier is not None and self.notifier.enabled:
            await self.notifier.send(alert)

    async def _alert_run(
        self, follower: str, previous: str | None, status: str, error: str | None
    ) -> None:
        """Alert on transitions only, so a persistent problem does not repeat every interval."""
        if status in {"partial", "failed"} and previous != status:
            await self._alert(
                Alert(
                    "sync_problem",
                    follower,
                    f"Sync to {follower} {status}: {error or 'see dashboard'}",
                    "problem",
                )
            )
        elif status not in {"partial", "failed"} and previous == "partial":
            await self._alert(
                Alert("sync_recovered", follower, f"Sync to {follower} recovered", "recovered")
            )

    async def _record_host_health(self, snapshot: HostSnapshot, *, role: str, url: str) -> None:
        previous = self.storage.host_status(snapshot.host)
        if not snapshot.reachable and previous != "offline":
            await self._alert(
                Alert(
                    "host_down",
                    snapshot.host,
                    f"{role.capitalize()} {snapshot.host} is unreachable: {snapshot.error}",
                    "problem",
                )
            )
        elif snapshot.reachable and previous == "offline":
            await self._alert(
                Alert("host_up", snapshot.host, f"{snapshot.host} is back online", "recovered")
            )
        self.storage.record_host_health(
            name=snapshot.host,
            role=role,
            url=url,
            online=snapshot.reachable,
            error=snapshot.error,
        )

    async def _apply_actions(
        self, follower_name: str, actions: list[ChangeAction]
    ) -> tuple[list[tuple[ChangeAction, str]], list[str]]:
        """Apply every action, continuing past failures. Returns per-action outcomes + errors."""
        client = self.clients[follower_name]
        outcomes: list[tuple[ChangeAction, str]] = []
        failures: list[str] = []
        refresh_blocklists = False
        refresh_allowlists = False
        for action in sorted(actions, key=self._apply_order):
            try:
                await self._apply_one(client, action)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "sync_action_failed",
                    extra={
                        "follower": follower_name,
                        "domain": action.domain.value,
                        "op": action.op.value,
                        "target": action.target,
                        "error": str(exc),
                    },
                )
                outcomes.append((action, OUTCOME_FAILED))
                failures.append(f"{action.domain.value}/{action.op.value} {action.target}: {exc}")
                continue
            outcomes.append((action, OUTCOME_SUCCESS))
            if action.domain == Domain.BLOCKLISTS:
                refresh_blocklists = True
            elif action.domain == Domain.ALLOWLISTS:
                refresh_allowlists = True

        for whitelist, needed in ((False, refresh_blocklists), (True, refresh_allowlists)):
            if not needed:
                continue
            try:
                await client.refresh_filters(whitelist=whitelist)
            except Exception as exc:  # noqa: BLE001
                kind = "allowlists" if whitelist else "blocklists"
                failures.append(f"{kind} refresh: {exc}")
        return outcomes, failures

    async def _apply_one(self, client: AdGuardClient, action: ChangeAction) -> None:
        if action.domain in {Domain.BLOCKLISTS, Domain.ALLOWLISTS}:
            whitelist = action.domain == Domain.ALLOWLISTS
            if action.op == Op.ADD:
                await client.add_filter(
                    action.detail["url"],
                    action.detail.get("name", ""),
                    whitelist=whitelist,
                )
            elif action.op == Op.UPDATE:
                await client.set_filter(
                    action.detail["url"],
                    action.detail.get("name", ""),
                    bool(action.detail.get("enabled", True)),
                    whitelist=whitelist,
                )
            elif action.op == Op.REMOVE:
                await client.remove_filter(action.detail["url"], whitelist=whitelist)
        elif action.domain == Domain.FILTER_INTERVAL:
            await client.set_filtering_interval(
                int(action.detail["interval"]), enabled=bool(action.detail["enabled"])
            )
        elif action.domain == Domain.USER_RULES:
            await client.set_user_rules(list(action.detail["rules"]))
        elif action.domain == Domain.REWRITES:
            if action.op == Op.ADD:
                await client.add_rewrite(
                    action.detail["domain"], action.detail["answer"], action.detail.get("enabled")
                )
            elif action.op == Op.UPDATE:
                await client.update_rewrite(
                    action.detail["domain"],
                    action.detail["answer"],
                    enabled=bool(action.detail["enabled"]),
                )
            elif action.op == Op.REMOVE:
                await client.delete_rewrite(action.detail["domain"], action.detail["answer"])
        elif action.domain == Domain.UPSTREAM_DNS:
            await client.set_dns_config(UpstreamDnsConfig.model_validate(action.detail))
        elif action.domain == Domain.BLOCKED_SERVICES:
            await client.set_blocked_services(BlockedServices.model_validate(action.detail))
        elif action.domain == Domain.DNS_SETTINGS:
            await client.set_dns_settings(dict(action.detail))
        elif action.domain == Domain.QUERYLOG:
            await client.set_querylog_config(QuerylogConfig.model_validate(action.detail))

    def _apply_order(self, action: ChangeAction) -> int:
        return {Op.REMOVE: 0, Op.UPDATE: 1, Op.REPLACE: 2, Op.ADD: 3}[action.op]
