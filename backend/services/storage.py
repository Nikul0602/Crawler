"""Atomic JSON storage helpers with per-file threading locks.

All JSON stores (tasks, schedules, settings) use these helpers to prevent
partial writes on crash and concurrent-write collisions within a single process.
"""

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

# Module-level registry of per-file locks
_lock_registry: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _get_lock(filepath: Path) -> threading.Lock:
    """Return (or create) a threading.Lock for the given file path."""
    key = str(filepath.resolve())
    with _registry_lock:
        if key not in _lock_registry:
            _lock_registry[key] = threading.Lock()
        return _lock_registry[key]


def write_json_atomic(filepath: Path, data: Any) -> None:
    """Write *data* as JSON to *filepath* atomically.

    Uses a unique temporary file in the same directory so the final
    os.replace() is on the same filesystem and therefore atomic on POSIX.
    On Windows, os.replace() is also atomic for the rename step.

    The per-file lock serialises concurrent writes from multiple threads.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    lock = _get_lock(filepath)
    with lock:
        # Unique tmp name to avoid collisions between concurrent callers
        tmp_path = filepath.with_name(f"{filepath.stem}_{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        except Exception:
            # Best-effort cleanup of the temporary file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def read_json_safe(filepath: Path, default: Any = None) -> Any:
    """Read JSON from *filepath*, returning *default* on any error."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}
