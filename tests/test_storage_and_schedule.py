from pathlib import Path

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

