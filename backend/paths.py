"""Project-level paths shared by backend modules."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
OUTPUT_DIR = PROJECT_ROOT / "output"
TASKS_FILE = PROJECT_ROOT / "data" / "tasks.json"
CHECKPOINT_FILE = PROJECT_ROOT / "crawl_state.json"

