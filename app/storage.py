from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.sync.result import ChangeAction, DriftItem


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    follower TEXT NOT NULL,
                    status TEXT NOT NULL,
                    added INTEGER NOT NULL DEFAULT 0,
                    updated INTEGER NOT NULL DEFAULT 0,
                    removed INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
                    domain TEXT NOT NULL,
                    op TEXT NOT NULL,
                    target TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drift (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
                    follower TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    target TEXT NOT NULL,
                    primary_value TEXT,
                    follower_value TEXT
                );
                CREATE TABLE IF NOT EXISTS host_health (
                    name TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_checked TEXT NOT NULL,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sync_runs_finished_at
                  ON sync_runs(finished_at);
                CREATE INDEX IF NOT EXISTS idx_sync_runs_follower_id
                  ON sync_runs(follower, id);
                CREATE INDEX IF NOT EXISTS idx_drift_follower_run_id
                  ON drift(follower, run_id);
                """
            )

    def record_host_health(
        self,
        *,
        name: str,
        role: str,
        url: str,
        online: bool,
        last_checked: str | None = None,
        error: str | None = None,
    ) -> None:
        status = "online" if online else "offline"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO host_health (name, role, url, status, last_checked, error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    role = excluded.role,
                    url = excluded.url,
                    status = excluded.status,
                    last_checked = excluded.last_checked,
                    error = excluded.error
                """,
                (name, role, url, status, last_checked or utc_now(), error),
            )

    def host_health(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM host_health
                ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, name
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def record_run(
        self,
        *,
        follower: str,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        added: int = 0,
        updated: int = 0,
        removed: int = 0,
        error: str | None = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_runs
                  (started_at, finished_at, follower, status, added, updated, removed, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_at or utc_now(),
                    finished_at or utc_now(),
                    follower,
                    status,
                    added,
                    updated,
                    removed,
                    error,
                ),
            )
            return int(cursor.lastrowid)  # type: ignore[arg-type]

    def record_changes(
        self,
        run_id: int,
        changes: Iterable[ChangeAction],
        *,
        outcome: str,
    ) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO changes (run_id, domain, op, target, outcome, detail)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        change.domain.value,
                        change.op.value,
                        change.target,
                        outcome,
                        json.dumps(change.detail, default=str, sort_keys=True),
                    )
                    for change in changes
                ],
            )

    def record_drift(self, run_id: int, follower: str, drift: Iterable[DriftItem]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO drift
                  (run_id, follower, domain, kind, target, primary_value, follower_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        follower,
                        item.domain.value,
                        item.kind,
                        item.target,
                        json.dumps(item.primary_value, default=str, sort_keys=True),
                        json.dumps(item.follower_value, default=str, sort_keys=True),
                    )
                    for item in drift
                ],
            )

    def latest_run_per_follower(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sr.*
                FROM sync_runs sr
                INNER JOIN (
                  SELECT follower, MAX(id) AS max_id
                  FROM sync_runs
                  GROUP BY follower
                ) latest ON latest.max_id = sr.id
                ORDER BY sr.follower
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def recent_runs(
        self,
        limit: int = 50,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        clauses: list[str] = []
        params: list[Any] = []
        if follower := filters.get("follower"):
            clauses.append("follower = ?")
            params.append(follower)
        if status := filters.get("status"):
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM sync_runs {where} ORDER BY id DESC LIMIT ?",  # noqa: S608
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def current_drift(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.*
                FROM drift d
                INNER JOIN (
                  SELECT follower, MAX(id) AS run_id
                  FROM sync_runs
                  GROUP BY follower
                ) latest ON latest.follower = d.follower AND latest.run_id = d.run_id
                ORDER BY d.follower, d.domain, d.target
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def purge_history_older_than(
        self,
        retention_days: int,
        *,
        now: datetime | None = None,
    ) -> int:
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        cutoff = (now.astimezone(UTC) - timedelta(days=retention_days)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM sync_runs WHERE COALESCE(finished_at, started_at) < ?",
                (cutoff,),
            )
            deleted = cursor.rowcount
        with self._connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return deleted


def serialise_dataclass(value: Any) -> dict[str, Any]:
    return asdict(value)
