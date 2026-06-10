from __future__ import annotations

from datetime import UTC, datetime

from app.storage import Storage
from app.sync.result import ChangeAction, Domain, DriftItem, Op


def test_init_creates_schema(tmp_path):
    storage = Storage(tmp_path / "adguard-sync.db")
    storage.init_db()

    assert storage.latest_run_per_follower() == []


def test_record_and_read_back_run_changes_and_drift(tmp_path):
    storage = Storage(tmp_path / "adguard-sync.db")
    storage.init_db()
    run_id = storage.record_run(follower="follower-a", status="drift_corrected", added=1)
    storage.record_changes(
        run_id,
        [ChangeAction(Domain.BLOCKLISTS, Op.ADD, "https://a.test/list.txt", {"url": "x"})],
        outcome="success",
    )
    storage.record_drift(
        run_id,
        "follower-a",
        [DriftItem(Domain.BLOCKLISTS, "missing", "https://a.test/list.txt", {"a": 1}, None)],
    )

    latest = storage.latest_run_per_follower()
    drift = storage.current_drift()

    assert latest[0]["follower"] == "follower-a"
    assert latest[0]["added"] == 1
    assert drift[0]["kind"] == "missing"


def test_current_drift_clears_when_latest_run_is_in_sync(tmp_path):
    storage = Storage(tmp_path / "adguard-sync.db")
    storage.init_db()
    drift_run = storage.record_run(follower="follower-a", status="drift_corrected")
    storage.record_drift(
        drift_run,
        "follower-a",
        [DriftItem(Domain.BLOCKLISTS, "missing", "https://a.test/list.txt", {"a": 1}, None)],
    )
    # A later in-sync run records no drift rows; current drift must reflect that.
    storage.record_run(follower="follower-a", status="in_sync")

    assert storage.current_drift() == []


def test_record_host_health_upserts_current_state(tmp_path):
    storage = Storage(tmp_path / "adguard-sync.db")
    storage.init_db()
    storage.record_host_health(
        name="primary",
        role="primary",
        url="http://primary.local",
        online=True,
        last_checked="2026-06-10T00:00:00+00:00",
    )
    storage.record_host_health(
        name="primary",
        role="primary",
        url="http://primary.local",
        online=False,
        last_checked="2026-06-10T01:00:00+00:00",
        error="primary GET /control/status failed with HTTP 401",
    )

    assert storage.host_health() == [
        {
            "name": "primary",
            "role": "primary",
            "url": "http://primary.local",
            "status": "offline",
            "last_checked": "2026-06-10T01:00:00+00:00",
            "error": "primary GET /control/status failed with HTTP 401",
        }
    ]


def test_purge_history_older_than_deletes_related_rows_and_keeps_recent(tmp_path):
    storage = Storage(tmp_path / "adguard-sync.db")
    storage.init_db()
    old_run_id = storage.record_run(
        follower="follower-a",
        status="drift_corrected",
        started_at="2026-05-01T00:00:00+00:00",
        finished_at="2026-05-01T00:01:00+00:00",
    )
    storage.record_changes(
        old_run_id,
        [ChangeAction(Domain.BLOCKLISTS, Op.UPDATE, "https://a.test/list.txt", {"url": "x"})],
        outcome="success",
    )
    storage.record_drift(
        old_run_id,
        "follower-a",
        [DriftItem(Domain.BLOCKLISTS, "changed", "https://a.test/list.txt", {"a": 1}, {"a": 2})],
    )
    recent_run_id = storage.record_run(
        follower="follower-a",
        status="in_sync",
        started_at="2026-06-09T00:00:00+00:00",
        finished_at="2026-06-09T00:01:00+00:00",
    )

    deleted = storage.purge_history_older_than(
        14,
        now=datetime(2026, 6, 10, tzinfo=UTC),
    )

    assert deleted == 1
    assert storage.recent_runs() == [
        {
            "id": recent_run_id,
            "started_at": "2026-06-09T00:00:00+00:00",
            "finished_at": "2026-06-09T00:01:00+00:00",
            "follower": "follower-a",
            "status": "in_sync",
            "added": 0,
            "updated": 0,
            "removed": 0,
            "error": None,
        }
    ]
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM drift").fetchone()[0] == 0


def test_latest_run_per_follower_returns_newest(tmp_path):
    storage = Storage(tmp_path / "adguard-sync.db")
    storage.init_db()
    storage.record_run(follower="follower-a", status="failed")
    latest_id = storage.record_run(follower="follower-a", status="in_sync")
    storage.record_run(follower="follower-b", status="in_sync")

    latest = storage.latest_run_per_follower()

    assert {row["follower"] for row in latest} == {"follower-a", "follower-b"}
    assert next(row for row in latest if row["follower"] == "follower-a")["id"] == latest_id
