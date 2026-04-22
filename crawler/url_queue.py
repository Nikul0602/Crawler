"""
Async URL queue with deduplication, priority ordering, and scope enforcement.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from discovery.url_utils import normalize_url

logger = logging.getLogger(__name__)


@dataclass(order=True)
class QueueEntry:
    """A prioritized URL entry in the crawl queue."""
    priority: int                              # lower = crawled sooner
    depth: int = field(compare=False)          # link-follow depth from homepage
    url: str = field(compare=False)            # normalized URL


class URLQueue:
    """
    Async crawl queue with dedup, depth/page limits, and priority.

    Features:
    - Deduplicates via normalized URL set
    - Respects max_pages and max_depth limits
    - Priority ordering (lower = higher priority)
    - Async-safe with asyncio primitives
    """

    def __init__(
        self,
        max_pages: int = 200,
        max_depth: int = 5,
    ):
        self.max_pages = max_pages
        self.max_depth = max_depth

        self._queue: asyncio.PriorityQueue[QueueEntry] = asyncio.PriorityQueue()
        self._seen: set[str] = set()
        self._processing: int = 0         # URLs currently being processed
        self._completed: int = 0          # URLs fully processed
        self._rejected_depth: int = 0     # URLs rejected for depth
        self._rejected_limit: int = 0     # URLs rejected for page limit

    async def add(
        self,
        url: str,
        depth: int = 0,
        priority: int = 2,
        base_url: str = "",
    ) -> bool:
        """
        Add a URL to the queue if it passes scope checks.

        Returns True if the URL was added, False if skipped (dup/out-of-scope).
        """
        normalized = normalize_url(url, base_url)
        if not normalized:
            return False

        # Already seen
        if normalized in self._seen:
            return False

        # Depth limit
        if depth > self.max_depth:
            self._rejected_depth += 1
            return False

        # Page limit (count seen URLs as our budget)
        if len(self._seen) >= self.max_pages:
            self._rejected_limit += 1
            return False

        self._seen.add(normalized)
        entry = QueueEntry(priority=priority, depth=depth, url=normalized)
        await self._queue.put(entry)
        return True

    async def add_batch(
        self,
        urls: list[str],
        depth: int = 0,
        priority: int = 2,
        base_url: str = "",
    ) -> int:
        """Add multiple URLs. Returns count of URLs actually added."""
        added = 0
        for url in urls:
            if await self.add(url, depth=depth, priority=priority, base_url=base_url):
                added += 1
        return added

    async def get(self) -> QueueEntry | None:
        """
        Get the next URL to crawl.

        Returns None if the queue is empty.
        """
        try:
            entry = self._queue.get_nowait()
            self._processing += 1
            return entry
        except asyncio.QueueEmpty:
            return None

    def mark_done(self) -> None:
        """Mark the current URL as fully processed."""
        self._processing -= 1
        self._completed += 1
        self._queue.task_done()

    def has_known(self, url: str) -> bool:
        """Check if a URL has already been seen."""
        return normalize_url(url) in self._seen

    @property
    def is_empty(self) -> bool:
        """True if no URLs are queued or being processed."""
        return self._queue.empty() and self._processing == 0

    @property
    def pending(self) -> int:
        """Number of URLs waiting in the queue."""
        return self._queue.qsize()

    @property
    def stats(self) -> dict:
        """Return queue statistics."""
        return {
            "seen": len(self._seen),
            "pending": self.pending,
            "processing": self._processing,
            "completed": self._completed,
            "rejected_depth": self._rejected_depth,
            "rejected_limit": self._rejected_limit,
        }

    @property
    def seen_urls(self) -> set[str]:
        """Return the full set of seen URLs (for checkpointing)."""
        return set(self._seen)
