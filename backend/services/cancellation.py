"""Cooperative cancellation and worker tracking for crawl tasks."""

import asyncio
import threading
from typing import Dict, Optional


class CrawlCancellationRegistry:
    """Own one cancellation event per active crawl task."""

    def __init__(self) -> None:
        self._events: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def register(self, task_id: str) -> threading.Event:
        with self._lock:
            event = threading.Event()
            self._events[task_id] = event
            return event

    def get(self, task_id: str) -> Optional[threading.Event]:
        with self._lock:
            return self._events.get(task_id)

    def request(self, task_id: str) -> bool:
        with self._lock:
            event = self._events.get(task_id)
            if event is None:
                return False
            event.set()
            return True

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._events.pop(task_id, None)


class CrawlWorkerRegistry:
    """Track asyncio tasks created for worker-thread crawls."""

    def __init__(self) -> None:
        self._workers: Dict[str, asyncio.Task] = {}
        self._lock = threading.Lock()

    def add(self, task_id: str, worker: asyncio.Task) -> None:
        with self._lock:
            self._workers[task_id] = worker

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._workers.pop(task_id, None)

    def get(self, task_id: str) -> Optional[asyncio.Task]:
        with self._lock:
            return self._workers.get(task_id)

    def active_ids(self) -> list[str]:
        with self._lock:
            return list(self._workers.keys())

    async def cancel_and_wait(self, task_id: str, timeout: float = 10.0) -> bool:
        worker = self.get(task_id)
        if worker is None:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


cancellation_registry = CrawlCancellationRegistry()
worker_registry = CrawlWorkerRegistry()
