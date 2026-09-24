"""Schedule manager — persists recurring crawl schedules to data/schedules.json.

Scheduling engine features:
- Pure asyncio loop inside FastAPI lifespan (no APScheduler dependency).
- Per-schedule claim lock: next_run_at is advanced before dispatch to prevent
  duplicate execution across ticks.
- Basic cron next-run calculation: tries croniter if available, falls back to
  simple timedelta for daily / weekly / monthly / custom.
"""

import uuid
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.paths import SCHEDULES_FILE
from backend.services.storage import write_json_atomic, read_json_safe

# Attempt to import croniter for accurate cron parsing
try:
    from croniter import croniter as _croniter  # type: ignore
    _HAS_CRONITER = True
except ImportError:
    _HAS_CRONITER = False


# ── Cron next-run helpers ────────────────────────────────────────────────────

def _next_run_from_cron(cron_expr: str, base: datetime) -> datetime:
    """Compute the next run datetime after *base* for *cron_expr*.

    Falls back to simple timedelta if croniter is not installed.
    """
    if _HAS_CRONITER:
        try:
            itr = _croniter(cron_expr, base)
            return itr.get_next(datetime)
        except Exception:
            pass
    raise ValueError("croniter is required for custom cron schedules")


def _validate_cron_expr(cron_expr: str) -> None:
    # ``0`` was previously left in the UI field as a placeholder. Keep
    # accepting it for compatibility while the custom-cron controls are
    # disabled in the frontend; it is handled as the normal daily fallback.
    if str(cron_expr).strip() == "0":
        return
    if not _HAS_CRONITER:
        raise ValueError("croniter is required for custom cron schedules")
    try:
        _croniter(cron_expr, _now_utc())
    except Exception as exc:
        raise ValueError(f"Invalid cron expression: {exc}") from exc


def _next_run_from_frequency(frequency: str, cron_expr: str, base: datetime) -> datetime:
    """Compute next run based on frequency shorthand or cron_expr."""
    if frequency == "daily":
        return base + timedelta(days=1)
    if frequency == "weekly":
        return base + timedelta(weeks=1)
    if frequency == "monthly":
        # Advance by approximately one month
        month = base.month + 1 if base.month < 12 else 1
        year = base.year + 1 if base.month == 12 else base.year
        try:
            return base.replace(year=year, month=month)
        except ValueError:
            # Handle Feb 29 → Feb 28
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            return base.replace(year=year, month=month, day=min(base.day, last_day))
    if frequency == "custom" and cron_expr:
        if str(cron_expr).strip() == "0":
            return base + timedelta(days=1)
        return _next_run_from_cron(cron_expr, base)
    # Default: daily
    return base + timedelta(days=1)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_one_time(value: str, timezone_name: str = "UTC") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("run_at is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("run_at must be a valid date and time") from exc
    if parsed.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name or "UTC"))
        except Exception as exc:
            raise ValueError(f"Unknown timezone: {timezone_name}") from exc
    return parsed.astimezone(timezone.utc)


# ── ScheduleManager ──────────────────────────────────────────────────────────

class ScheduleManager:
    def __init__(self, storage_file: Path = SCHEDULES_FILE):
        self.storage_file = Path(storage_file)
        self._store_lock = threading.Lock()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        return read_json_safe(self.storage_file, {})

    def _save(self, data: Dict[str, Any]):
        write_json_atomic(self.storage_file, data)

    def _new_id(self) -> str:
        return "sched_" + uuid.uuid4().hex[:8]

    def _validate_config(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "max_pages": max(1, min(int(cfg.get("max_pages", 200)), 5000)),
            "max_depth": max(1, min(int(cfg.get("max_depth", 5)), 20)),
            "concurrent": max(1, min(int(cfg.get("concurrent", 3)), 20)),
            "delay": max(0.0, min(float(cfg.get("delay", 1.5)), 60.0)),
            "no_robots": bool(cfg.get("no_robots", False)),
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def get_all(self) -> List[Any]:
        schedules = list(self._load().values())
        schedules.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        return schedules

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        timezone_name = str(data.get("timezone", "UTC") or "UTC")
        run_at_value = data.get("run_at")
        if run_at_value:
            run_at = _parse_one_time(str(run_at_value), timezone_name)
            if run_at <= _now_utc():
                raise ValueError("run_at must be in the future")
            now = _now_utc()
            sched: Dict[str, Any] = {
                "id": self._new_id(),
                "name": str(data.get("name", "")).strip() or "Unnamed Schedule",
                "url": str(data.get("url", "")).strip(),
                "enabled": True,
                "frequency": "once",
                "cron_expr": "",
                "run_at": _iso(run_at),
                "timezone": timezone_name,
                "config": self._validate_config(data.get("config", {})),
                "created_at": _iso(now),
                "last_run_at": None,
                "last_run_status": None,
                "last_task_id": None,
                "next_run_at": _iso(run_at),
            }
            with self._store_lock:
                store = self._load()
                store[sched["id"]] = sched
                self._save(store)
            return sched

        frequency = data.get("frequency", "daily")
        if frequency not in {"daily", "weekly", "monthly", "custom"}:
            raise ValueError("Unsupported schedule frequency")
        cron_expr = data.get("cron_expr", "")
        if not cron_expr:
            # Generate a sensible default cron expression
            _freq_to_cron = {
                "daily": "0 2 * * *",
                "weekly": "0 2 * * 1",
                "monthly": "0 2 1 * *",
            }
            cron_expr = _freq_to_cron.get(frequency, "0 2 * * *")

        if frequency == "custom":
            if not cron_expr:
                raise ValueError("cron_expr is required for custom schedules")
            _validate_cron_expr(cron_expr)

        now = _now_utc()
        next_run = _next_run_from_frequency(frequency, cron_expr, now)

        sched: Dict[str, Any] = {
            "id": self._new_id(),
            "name": str(data.get("name", "")).strip() or "Unnamed Schedule",
            "url": str(data.get("url", "")).strip(),
            "enabled": True,
            "frequency": frequency,
            "cron_expr": cron_expr,
            "config": self._validate_config(data.get("config", {})),
            "timezone": data.get("timezone", "UTC"),
            "created_at": _iso(now),
            "last_run_at": None,
            "last_run_status": None,
            "last_task_id": None,
            "next_run_at": _iso(next_run),
        }

        with self._store_lock:
            store = self._load()
            store[sched["id"]] = sched
            self._save(store)

        return sched

    def update(self, sched_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._store_lock:
            store = self._load()
            sched = store.get(sched_id)
            if not sched:
                return None
            if "name" in data:
                sched["name"] = str(data["name"]).strip() or sched["name"]
            if "url" in data:
                sched["url"] = str(data["url"]).strip()
            if "frequency" in data:
                if data["frequency"] not in {"daily", "weekly", "monthly", "custom"}:
                    raise ValueError("Unsupported schedule frequency")
                sched["frequency"] = data["frequency"]
            if "cron_expr" in data:
                sched["cron_expr"] = data["cron_expr"]
            if "config" in data:
                sched["config"] = self._validate_config(data["config"])
            if "enabled" in data:
                sched["enabled"] = bool(data["enabled"])

            if "run_at" in data and data.get("run_at"):
                timezone_name = str(data.get("timezone", sched.get("timezone", "UTC")) or "UTC")
                run_at = _parse_one_time(str(data["run_at"]), timezone_name)
                if run_at <= _now_utc() and sched.get("last_run_status") != "Running":
                    raise ValueError("run_at must be in the future")
                sched["frequency"] = "once"
                sched["cron_expr"] = ""
                sched["run_at"] = _iso(run_at)
                sched["timezone"] = timezone_name
                sched["next_run_at"] = _iso(run_at)
                store[sched_id] = sched
                self._save(store)
                return sched

            # Recalculate next run if timing fields changed
            if "frequency" in data or "cron_expr" in data:
                if sched["frequency"] == "custom":
                    if not sched.get("cron_expr"):
                        raise ValueError("cron_expr is required for custom schedules")
                    _validate_cron_expr(sched["cron_expr"])
                base = _now_utc()
                sched["next_run_at"] = _iso(
                    _next_run_from_frequency(
                        sched["frequency"], sched["cron_expr"], base
                    )
                )

            store[sched_id] = sched
            self._save(store)
        return sched

    def delete(self, sched_id: str) -> bool:
        with self._store_lock:
            store = self._load()
            if sched_id not in store:
                return False
            del store[sched_id]
            self._save(store)
        return True

    def toggle(self, sched_id: str) -> Optional[Dict[str, Any]]:
        with self._store_lock:
            store = self._load()
            sched = store.get(sched_id)
            if not sched:
                return None
            sched["enabled"] = not sched.get("enabled", True)
            store[sched_id] = sched
            self._save(store)
        return sched

    def get_due_schedules(self) -> List[Any]:
        """Return enabled schedules whose next_run_at is at or before now."""
        now = _now_utc()
        due = []
        for sched in self._load().values():
            if not sched.get("enabled"):
                continue
            next_run_str = sched.get("next_run_at")
            if not next_run_str:
                continue
            try:
                next_run = datetime.fromisoformat(next_run_str)
                # Make timezone-aware if naive
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
                if next_run <= now:
                    due.append(sched)
            except ValueError:
                continue
        return due

    def claim_schedule(self, sched_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        """Atomically advance next_run_at and record the dispatched task_id.

        Must be called before dispatching the crawl to prevent duplicate runs.
        """
        with self._store_lock:
            store = self._load()
            sched = store.get(sched_id)
            if not sched:
                return None
            if sched.get("last_run_status") == "Running":
                return None

            now = _now_utc()
            next_run = _next_run_from_frequency(
                sched.get("frequency", "daily"),
                sched.get("cron_expr", ""),
                now,
            )
            sched["last_run_at"] = _iso(now)
            sched["last_task_id"] = task_id
            sched["next_run_at"] = _iso(next_run)
            sched["last_run_status"] = "Running"
            if sched.get("frequency") == "once":
                sched["next_run_at"] = None
            else:
                sched["next_run_at"] = _iso(next_run)

            store[sched_id] = sched
            self._save(store)
        return sched

    def claim_due_schedule(self, sched_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        """Re-check and claim a due schedule in one locked operation."""
        with self._store_lock:
            store = self._load()
            sched = store.get(sched_id)
            if not sched or not sched.get("enabled"):
                return None
            due_value = sched.get("next_run_at")
            if not due_value:
                return None
            due_at = datetime.fromisoformat(due_value)
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            if due_at > _now_utc():
                return None
            now = _now_utc()
            sched["last_run_at"] = _iso(now)
            sched["last_task_id"] = task_id
            if sched.get("frequency") == "once":
                sched["next_run_at"] = None
            else:
                sched["next_run_at"] = _iso(_next_run_from_frequency(
                    sched.get("frequency", "daily"), sched.get("cron_expr", ""), now
                ))
            sched["last_run_status"] = "Running"
            store[sched_id] = sched
            self._save(store)
            return sched

    def update_last_run_status(self, sched_id: str, status: str):
        with self._store_lock:
            store = self._load()
            sched = store.get(sched_id)
            if sched:
                sched["last_run_status"] = status
                store[sched_id] = sched
                self._save(store)


schedule_manager = ScheduleManager()
