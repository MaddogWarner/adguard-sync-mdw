from __future__ import annotations

import logging

from app.adguard.client import AdGuardClient
from app.adguard.models import BlockedServices, HostSnapshot, UpstreamDnsConfig
from app.config import AppConfig
from app.storage import Storage, utc_now
from app.sync.differ import diff_host
from app.sync.result import ChangeAction, Domain, DriftItem, Op

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(
        self,
        config: AppConfig,
        storage: Storage,
        clients: dict[str, AdGuardClient],
    ) -> None:
        self.config = config
        self.storage = storage
        self.clients = clients

    async def run_once(self) -> list[int]:
        primary = await self.clients[self.config.primary.name].snapshot()
        self._record_host_health(primary, role="primary", url=self.config.primary.url)
        run_ids: list[int] = []
        if not primary.reachable:
            for follower in self.config.followers:
                run_ids.append(
                    self.storage.record_run(
                        follower=follower.name,
                        status="failed",
                        error="primary unreachable",
                    )
                )
            return run_ids

        for follower in self.config.followers:
            try:
                run_ids.append(await self.sync_follower(primary, follower.name))
            except Exception as exc:  # noqa: BLE001
                logger.exception("sync_follower_failed", extra={"follower": follower.name})
                run_ids.append(
                    self.storage.record_run(
                        follower=follower.name,
                        status="failed",
                        error=str(exc),
                    )
                )
        return run_ids

    async def sync_follower(self, primary_snapshot: HostSnapshot, follower_name: str) -> int:
        started_at = utc_now()
        follower_snapshot = await self.clients[follower_name].snapshot()
        follower_config = next(
            follower for follower in self.config.followers if follower.name == follower_name
        )
        self._record_host_health(
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
        status = self._status(actions, drift)
        outcome = "skipped(dry_run)" if self.config.dry_run else "success"
        error: str | None = None

        if actions and not self.config.dry_run:
            try:
                await self._apply_actions(follower_name, actions)
            except Exception as exc:  # noqa: BLE001
                status = "partial"
                outcome = "failed"
                error = str(exc)

        counts = self._counts(actions)
        run_id = self.storage.record_run(
            follower=follower_name,
            status=status,
            started_at=started_at,
            added=counts["added"],
            updated=counts["updated"],
            removed=counts["removed"],
            error=error,
        )
        self.storage.record_changes(run_id, actions, outcome=outcome)
        self.storage.record_drift(run_id, follower_name, drift)
        return run_id

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

    def _record_host_health(self, snapshot: HostSnapshot, *, role: str, url: str) -> None:
        self.storage.record_host_health(
            name=snapshot.host,
            role=role,
            url=url,
            online=snapshot.reachable,
            error=snapshot.error,
        )

    async def _apply_actions(self, follower_name: str, actions: list[ChangeAction]) -> None:
        client = self.clients[follower_name]
        refresh_blocklists = False
        refresh_allowlists = False
        for action in sorted(actions, key=self._apply_order):
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
                refresh_allowlists = refresh_allowlists or whitelist
                refresh_blocklists = refresh_blocklists or not whitelist
            elif action.domain == Domain.USER_RULES:
                await client.set_user_rules(list(action.detail["rules"]))
            elif action.domain == Domain.REWRITES:
                if action.op == Op.ADD:
                    await client.add_rewrite(action.detail["domain"], action.detail["answer"])
                elif action.op == Op.REMOVE:
                    await client.delete_rewrite(action.detail["domain"], action.detail["answer"])
            elif action.domain == Domain.UPSTREAM_DNS:
                await client.set_dns_config(UpstreamDnsConfig.model_validate(action.detail))
            elif action.domain == Domain.BLOCKED_SERVICES:
                await client.set_blocked_services(BlockedServices.model_validate(action.detail))

        if refresh_blocklists:
            await client.refresh_filters(whitelist=False)
        if refresh_allowlists:
            await client.refresh_filters(whitelist=True)

    def _apply_order(self, action: ChangeAction) -> int:
        return {Op.REMOVE: 0, Op.UPDATE: 1, Op.REPLACE: 2, Op.ADD: 3}[action.op]
