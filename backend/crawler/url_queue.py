"""
Async URL queue with deduplication, priority ordering, and scope enforcement.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from backend.discovery.url_utils import normalize_url, should_exclude, is_same_domain

logger = logging.getLogger(__name__)


@dataclass(order=True)
class QueueEntry:
    """A prioritized URL entry in the crawl queue."""
    priority: int                              # lower = crawled sooner
    depth: int = field(compare=False)          # link-follow depth from homepage
    url: str = field(compare=False)            # normalized URL


class URLQueue:
    """
    Async crawl queue with dedup, depth/discovery limits, scope filtering,
    and priority.

    Features:
    - Deduplicates via normalized URL set
    - Respects max_discovered_urls and max_depth limits
    - Rejects excluded file types (PDFs, images, etc.) at the queue boundary
    - Rejects out-of-scope domains when allowed_domain is set
    - Priority ordering (lower = higher priority)
    - Snapshot / restore for checkpoint-based resume
    - Async-safe with asyncio primitives
    """

    def __init__(
        self,
        max_discovered_urls: int = 1000,
        max_depth: int = 5,
        allowed_domain: str = "",
    ):
        self.max_discovered_urls = max_discovered_urls
        self.max_depth = max_depth
        self.allowed_domain = allowed_domain

        self._queue: asyncio.PriorityQueue[QueueEntry] = asyncio.PriorityQueue()
        self._seen: set[str] = set()
        self._processing: int = 0         # URLs currently being processed
        self._completed: int = 0          # URLs fully processed
        self._rejected_depth: int = 0     # URLs rejected for depth
        self._rejected_limit: int = 0     # URLs rejected for discovery limit
        self._rejected_scope: int = 0     # URLs rejected for scope (domain/file type)

    def set_allowed_domain(self, allowed_domain: str) -> None:
        """Set the domain scope filter after base_domain is computed."""
        self.allowed_domain = allowed_domain

    async def add(
        self,
        url: str,
        depth: int = 0,
        priority: int = 2,
        base_url: str = "",
    ) -> bool:
        """
        Add a URL to the queue if it passes scope checks.

        Checks applied in order:
        1. Normalize URL
        2. Reject empty URLs
        3. Reject excluded file types (PDFs, images, etc.)
        4. Reject out-of-scope domains when allowed_domain is set
        5. Reject duplicates (already in _seen)
        6. Reject depth beyond max_depth
        7. Reject when len(_seen) >= max_discovered_urls
        8. Add to _seen and priority queue

        Returns True if the URL was added, False if skipped.
        """
        normalized = normalize_url(url, base_url)
        if not normalized:
            return False

        # Excluded file types (PDFs, images, CSS, JS, etc.)
        if should_exclude(normalized):
            self._rejected_scope += 1
            return False

        # Domain scope check
        if self.allowed_domain and not is_same_domain(normalized, self.allowed_domain):
            self._rejected_scope += 1
            return False

        # Already seen
        if normalized in self._seen:
            return False

        # Depth limit
        if depth > self.max_depth:
            self._rejected_depth += 1
            return False

        # Discovery limit (count seen URLs as our budget)
        if len(self._seen) >= self.max_discovered_urls:
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

    def mark_abandoned(self) -> None:
        """
        Mark a processing URL as no longer active without counting it complete.

        Used when an in-flight crawl task is cancelled for a time-budget stop
        and saved in active_queue for resume.
        """
        self._processing -= 1
        self._queue.task_done()

    def has_known(self, url: str) -> bool:
        """Check if a URL has already been seen."""
        return normalize_url(url) in self._seen

    def snapshot(self) -> list[dict]:
        """
        Return pending queue entries without consuming them.

        CPython implementation detail: asyncio.PriorityQueue stores its heap
        in ._queue. Tested target range: Python 3.10-3.13.
        """
        entries = []
        for item in self._queue._queue:
            if isinstance(item, QueueEntry):
                entries.append({
                    "url": item.url,
                    "depth": item.depth,
                    "priority": item.priority,
                })
        return entries

    def restore(
        self,
        seen_urls: set[str],
        pending_entries: list[dict],
        completed_count: int = 0,
    ) -> None:
        """
        Restore queue state from a checkpoint.

        Inserts pending entries directly into the priority queue without
        calling add(), because restored URLs are already in _seen.
        """
        self._seen = {normalize_url(u) for u in seen_urls if normalize_url(u)}
        self._queue = asyncio.PriorityQueue()
        self._processing = 0
        self._completed = completed_count
        self._rejected_depth = 0
        self._rejected_limit = 0
        self._rejected_scope = 0
        for entry in pending_entries:
            normalized = normalize_url(entry["url"])
            if normalized:
                self._queue.put_nowait(
                    QueueEntry(
                        priority=int(entry.get("priority", 2)),
                        depth=int(entry.get("depth", 0)),
                        url=normalized,
                    )
                )

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
            "rejected_scope": self._rejected_scope,
        }

    @property
    def seen_urls(self) -> set[str]:
        """Return the full set of seen URLs (for checkpointing)."""
        return set(self._seen)

