"""Pytest bootstrap for the repository source layout."""

import sys
import shutil
import uuid
from pathlib import Path
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_path():
    """Use a workspace-local temp directory on restricted Windows setups."""
    root = PROJECT_ROOT / "tests" / ".pytest_tmp"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
