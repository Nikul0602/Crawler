from pathlib import Path
from datetime import datetime, timedelta, timezone

from backend.services.schedule_manager import ScheduleManager
from backend.services.storage import write_json_atomic, read_json_safe


def test_atomic_json_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"ok": True})
    assert read_json_safe(path) == {"ok": True}


def test_due_schedule_claim_is_single_use(tmp_path: Path):
    manager = ScheduleManager(tmp_path / "schedules.json")
    schedule = manager.create({"url": "https://example.com", "frequency": "daily"})
    assert manager.claim_due_schedule(schedule["id"], "task-1") is None


def test_one_time_schedule_stores_run_at_and_claims_once(tmp_path: Path):
    manager = ScheduleManager(tmp_path / "schedules.json")
    run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    schedule = manager.create({"url": "https://example.com", "run_at": run_at})

    assert schedule["frequency"] == "once"
    assert schedule["run_at"] == schedule["next_run_at"]
    claimed = manager.claim_schedule(schedule["id"], "task-1")
    assert claimed is not None
    assert claimed["next_run_at"] is None


def test_legacy_zero_cron_placeholder_does_not_fail(tmp_path: Path):
    manager = ScheduleManager(tmp_path / "schedules.json")
    schedule = manager.create({
        "url": "https://example.com",
        "frequency": "custom",
        "cron_expr": "0",
    })
    assert schedule["cron_expr"] == "0"
