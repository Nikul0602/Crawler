"""Project-level paths shared by backend modules."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
OUTPUT_DIR = PROJECT_ROOT / "output"
DATA_DIR = PROJECT_ROOT / "data"
TASKS_FILE = DATA_DIR / "tasks.json"
SCHEDULES_FILE = DATA_DIR / "schedules.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
CHECKPOINT_FILE = PROJECT_ROOT / "crawl_state.json"

