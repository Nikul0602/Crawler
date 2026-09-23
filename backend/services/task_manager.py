"""Task manager — persists crawl job state to data/tasks.json.

Uses atomic JSON writes via backend.services.storage to prevent file
corruption on crash.  Every mutating operation holds the per-file lock
inside write_json_atomic.
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.paths import TASKS_FILE
from backend.services.storage import write_json_atomic, read_json_safe


class TaskManager:
    def __init__(self, storage_file: str | Path = TASKS_FILE):
        self.storage_file = Path(storage_file)
        self.tasks: Dict[str, Any] = self._load_tasks()

    # ── Internal helpers ────────────────────────────────────────────────────

    def _load_tasks(self) -> Dict[str, Any]:
        tasks = read_json_safe(self.storage_file, {})

        # Clean up zombie tasks from previous server runs
        has_zombies = False
        for task in tasks.values():
            if task.get("status") in ["Running", "Queued"]:
                task["status"] = "Failed"
                task["error"] = "Server restarted before completion."
                task["completed_at"] = datetime.now().isoformat()
                has_zombies = True

        if has_zombies:
            self.tasks = tasks
            self._save_tasks()

        return tasks

    def _save_tasks(self):
        write_json_atomic(self.storage_file, self.tasks)

    # ── Public API ──────────────────────────────────────────────────────────

    def create_task(
        self,
        url: str,
        request_config: Optional[Dict[str, Any]] = None,
        schedule_id: Optional[str] = None,
        target_domain: Optional[str] = None,
    ) -> str:
        """Create a new task and return its ID."""
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = {
            "id": task_id,
            "url": url,
            "status": "Queued",          # Queued | Running | CancellationRequested | Cancelled | Completed | Failed
            "progress": 0,
            "pages_crawled": 0,
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "duration": None,
            "error": None,
            # Extra fields for re-run and report linking
            "request_config": request_config or {},
            "report_domain": None,
            "target_domain": target_domain,
            "schedule_id": schedule_id,
            "cancelled_at": None,
        }
        self._save_tasks()
        return task_id

    def update_task(self, task_id: str, **kwargs):
        if task_id in self.tasks:
            for key, value in kwargs.items():
                self.tasks[task_id][key] = value
            self._save_tasks()

    def get_task(self, task_id: str) -> Optional[Any]:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[Any]:
        """Return all tasks sorted by started_at descending (newest first)."""
        tasks_list = list(self.tasks.values())
        tasks_list.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        return tasks_list

    def delete_task(self, task_id: str) -> bool:
        """Delete a task only if it is not currently Running.

        Returns True if the task was deleted, False otherwise.
        """
        task = self.tasks.get(task_id)
        if task is None:
            return False
        if task.get("status") in {"Running", "CancellationRequested"}:
            return False
        del self.tasks[task_id]
        self._save_tasks()
        return True

    def rerun_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Return the stored request_config for a task so the caller can
        create a new task with identical parameters.  Returns None if the
        task does not exist or has no stored config.
        """
        task = self.tasks.get(task_id)
        if task is None:
            return None
        return task.get("request_config") or None

    def get_active_domains(self) -> set:
        """Return the set of domains currently being crawled (Running tasks)."""
        domains = set()
        for task in self.tasks.values():
            if task.get("status") == "Running":
                domain = task.get("report_domain") or task.get("target_domain")
                if domain:
                    domains.add(domain)
        return domains

    def get_active_tasks_for_schedule(self, schedule_id: str) -> list[Dict[str, Any]]:
        return [
            task for task in self.tasks.values()
            if task.get("schedule_id") == schedule_id
            and task.get("status") in {"Queued", "Running", "CancellationRequested"}
        ]


task_manager = TaskManager()
