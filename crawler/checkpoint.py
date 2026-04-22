"""
Checkpoint system — save and resume crawl state.

Writes crawl progress to a JSON file so an interrupted crawl can resume
from where it left off instead of starting over.
"""

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime

from models import PageData

logger = logging.getLogger(__name__)


class CrawlCheckpoint:
    """
    Manages crawl state persistence.

    Saves:
    - Completed pages (URL → PageData)
    - Seen URLs (for dedup)
    - Failed URLs
    - Crawl config snapshot
    """

    def __init__(self, filepath: str = "crawl_state.json"):
        self.filepath = filepath

    def save(
        self,
        completed_pages: list[PageData],
        seen_urls: set[str],
        failed_urls: list[dict],
        base_url: str,
        domain: str,
        crawl_started: str = "",
    ) -> None:
        """Save current crawl state to disk."""
        state = {
            "version": 1,
            "saved_at": datetime.now().isoformat(),
            "base_url": base_url,
            "domain": domain,
            "crawl_started": crawl_started,
            "completed_count": len(completed_pages),
            "seen_urls": sorted(seen_urls),
            "failed_urls": failed_urls,
            "pages": [asdict(p) for p in completed_pages],
        }

        # Write atomically (write to tmp, then rename)
        tmp_path = self.filepath + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            # Atomic rename (works on most systems)
            if os.path.exists(self.filepath):
                os.remove(self.filepath)
            os.rename(tmp_path, self.filepath)
            logger.info(
                f"[checkpoint] 💾 Saved {len(completed_pages)} pages to {self.filepath}"
            )
        except Exception as e:
            logger.error(f"[checkpoint] ❌ Failed to save: {e}")
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def load(self) -> dict | None:
        """
        Load a previous crawl state.

        Returns dict with keys: seen_urls, pages, failed_urls, base_url, domain, crawl_started.
        Returns None if no checkpoint exists or it's invalid.
        """
        if not os.path.exists(self.filepath):
            return None

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                state = json.load(f)

            if state.get("version") != 1:
                logger.warning("[checkpoint] ⚠️  Unknown checkpoint version, skipping")
                return None

            # Reconstruct PageData objects
            pages = []
            for page_dict in state.get("pages", []):
                pages.append(PageData(**{
                    k: v for k, v in page_dict.items()
                    if k in PageData.__dataclass_fields__
                }))

            result = {
                "seen_urls": set(state.get("seen_urls", [])),
                "pages": pages,
                "failed_urls": state.get("failed_urls", []),
                "base_url": state.get("base_url", ""),
                "domain": state.get("domain", ""),
                "crawl_started": state.get("crawl_started", ""),
            }

            logger.info(
                f"[checkpoint] 📂 Loaded checkpoint: "
                f"{len(pages)} pages, {len(result['seen_urls'])} seen URLs"
            )
            return result

        except Exception as e:
            logger.error(f"[checkpoint] ❌ Failed to load checkpoint: {e}")
            return None

    def exists(self) -> bool:
        """Check if a checkpoint file exists."""
        return os.path.exists(self.filepath)

    def delete(self) -> None:
        """Remove the checkpoint file."""
        if os.path.exists(self.filepath):
            os.remove(self.filepath)
            logger.info(f"[checkpoint] 🗑️  Deleted checkpoint {self.filepath}")
