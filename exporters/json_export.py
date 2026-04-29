"""
JSON export — serialize a WebsiteReport to disk.
"""

import json
import logging
import os
from dataclasses import asdict

from core.models import WebsiteReport

logger = logging.getLogger(__name__)


def export_json(report: WebsiteReport, output_dir: str) -> str:
    """
    Export a WebsiteReport as a JSON file.

    Args:
        report: The completed crawl report.
        output_dir: Directory to write the file into.

    Returns:
        Absolute path to the written JSON file.
    """
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, "data.json")

    data = asdict(report)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    size_kb = os.path.getsize(filepath) / 1024
    logger.info(f"[json] 📄 Exported {filepath} ({size_kb:.1f} KB)")
    return filepath
