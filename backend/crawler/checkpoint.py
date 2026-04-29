"""
Checkpoint system — save and resume crawl state.

Writes crawl progress to a JSON file so an interrupted crawl can resume
from where it left off instead of starting over.

Schema versions:
- v1: seen_urls, pages, failed_urls (legacy — resume is approximate)
- v2: adds pending_queue, active_queue, queue_stats, crawl_stopped_reason
      (enables true continuation from saved queue state)
- v3: adds navigation_links and detected_platform for full report-state resume
"""

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime

from backend.core.models import PageData
from backend.paths import CHECKPOINT_FILE

logger = logging.getLogger(__name__)

_CURRENT_VERSION = 3


class CrawlCheckpoint:
    """
    Manages crawl state persistence.

    Saves:
    - Completed pages (URL → PageData)
    - Seen URLs (for dedup)
    - Failed URLs
    - Pending queue entries with depth/priority (v2)
    - Active (in-flight) entries at time of save (v2)
    - Queue stats (v2)
    - Crawl stop reason (v2)
    - Navigation links discovered by interactive nav (v3)
    - Detected platform (v3)
    - Crawl config snapshot
    """

    def __init__(self, filepath: str = str(CHECKPOINT_FILE)):
        self.filepath = str(filepath)

    def save(
        self,
        completed_pages: list[PageData],
        seen_urls: set[str],
        failed_urls: list[dict],
        base_url: str,
        domain: str,
        crawl_started: str = "",
        pending_queue: list[dict] | None = None,
        active_queue: list[dict] | None = None,
        queue_stats: dict | None = None,
        crawl_stopped_reason: str = "",
        navigation_links: list[str] | None = None,
        detected_platform: str = "unknown",
    ) -> None:
        """Save current crawl state to disk (v3 schema)."""
        state = {
            "version": _CURRENT_VERSION,
            "saved_at": datetime.now().isoformat(),
            "base_url": base_url,
            "domain": domain,
            "crawl_started": crawl_started,
            "crawl_stopped_reason": crawl_stopped_reason,
            "completed_count": len(completed_pages),
            "seen_urls": sorted(seen_urls),
            "failed_urls": failed_urls,
            "pages": [asdict(p) for p in completed_pages],
            "pending_queue": pending_queue or [],
            "active_queue": active_queue or [],
            "queue_stats": queue_stats or {},
            "navigation_links": navigation_links or [],
            "detected_platform": detected_platform or "unknown",
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

        Supports both v1 and v2 checkpoints:
        - v2/v3: returns pending_queue (pending + active entries combined)
        - v1: returns empty pending_queue; orchestrator falls back to
              approximate reconstruction from seen_urls − completed_urls.

        Returns dict with keys: seen_urls, pages, failed_urls, base_url,
        domain, crawl_started, pending_queue, crawl_stopped_reason,
        navigation_links, detected_platform.
        Returns None if no checkpoint exists or it's invalid.
        """
        if not os.path.exists(self.filepath):
            return None

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                state = json.load(f)

            version = state.get("version", 0)
            if version not in (1, 2, 3):
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
                "crawl_stopped_reason": state.get("crawl_stopped_reason", ""),
                "navigation_links": state.get("navigation_links", []),
                "detected_platform": state.get("detected_platform", "unknown"),
            }

            # v2: merge pending + active into one restore list
            if version >= 2:
                pending = state.get("pending_queue", [])
                active = state.get("active_queue", [])
                result["pending_queue"] = pending + active
            else:
                # v1 fallback: no pending queue info available
                result["pending_queue"] = []

            logger.info(
                f"[checkpoint] 📂 Loaded v{version} checkpoint: "
                f"{len(pages)} pages, {len(result['seen_urls'])} seen URLs"
                + (f", {len(result['pending_queue'])} pending entries" if result["pending_queue"] else "")
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

