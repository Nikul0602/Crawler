import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from backend.paths import TASKS_FILE

class TaskManager:
    def __init__(self, storage_file: str | Path = TASKS_FILE):
        self.storage_file = Path(storage_file)
        self.tasks: Dict[str, Any] = self._load_tasks()

    def _load_tasks(self) -> Dict[str, Any]:
        tasks = {}
        if self.storage_file.exists():
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    tasks = json.load(f)
            except Exception:
                tasks = {}
                
        # Clean up zombie tasks from previous server runs
        has_zombies = False
        for task_id, task in tasks.items():
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
        with open(self.storage_file, "w", encoding="utf-8") as f:
            json.dump(self.tasks, f, indent=4)

    def create_task(self, url: str) -> str:
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = {
            "id": task_id,
            "url": url,
            "status": "Queued",  # Queued, Running, Completed, Failed
            "progress": 0,
            "pages_crawled": 0,
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "duration": None,
            "error": None
        }
        self._save_tasks()
        return task_id

    def update_task(self, task_id: str, **kwargs):
        if task_id in self.tasks:
            for key, value in kwargs.items():
                self.tasks[task_id][key] = value
            self._save_tasks()

    def get_task(self, task_id: str) -> Any:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[Any]:
        # Return sorted by started_at descending
        tasks_list = list(self.tasks.values())
        tasks_list.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        return tasks_list

task_manager = TaskManager()

