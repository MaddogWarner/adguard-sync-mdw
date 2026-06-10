from __future__ import annotations

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


def test_latest_run_per_follower_returns_newest(tmp_path):
    storage = Storage(tmp_path / "adguard-sync.db")
    storage.init_db()
    storage.record_run(follower="follower-a", status="failed")
    latest_id = storage.record_run(follower="follower-a", status="in_sync")
    storage.record_run(follower="follower-b", status="in_sync")

    latest = storage.latest_run_per_follower()

    assert {row["follower"] for row in latest} == {"follower-a", "follower-b"}
    assert next(row for row in latest if row["follower"] == "follower-a")["id"] == latest_id
