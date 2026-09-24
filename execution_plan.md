# CrawlMaster — Execution-Ready Implementation Plan

**Source Audit**: [IMPLEMENTATION_AUDIT_AND_FIXES.md](file:///d:/YT/Crawler/IMPLEMENTATION_AUDIT_AND_FIXES.md)  
**Verified Against**: All backend service files and frontend HTML/JS files read directly.  
**Status of Each Issue**: Confirmed by source inspection.

---

## Pre-Flight: Verified Current State

| Audit Issue | Current Code Reality |
|---|---|
| Issue 1: Cancellation incomplete | `cancellation_event.is_set()` checked in `_run_crawl_loop` loop boundary only; `_process_page` pipeline stages have no internal cancellation |
| Issue 2: Task visible before worker exits | `cancel_task()` returns `202` and sets `Cancelled` in database immediately; worker thread may still run |
| Issue 3: Schedule deletion before crawl stops | `DELETE /api/schedules/{id}` signals tasks then deletes schedule and returns `200` synchronously |
| Issue 4: Schedule claim race | `get_due_schedules()` and `claim_schedule()` are two separate unlocked calls in `_schedule_checker_loop` |
| Issue 5: Centralized finalization missing | Four separate finalization paths in `_async_crawl_task` — all scattered, cancel path skips schedule update in some branches |
| Issue 6: Shared checkpoint file | All crawls use root `crawl_state.json`; concurrent crawls corrupt each other's state |
| Issue 7: Non-atomic report publication | `generate_markdown_report` + `export_json` write directly to `output/{domain}/` while domain may be read |
| Issue 8: Report readers fully load JSON | `get_report_detail()` and `get_pages()` both do `json.load(full 3MB+ file)` |
| Issue 9: CSV not truly streaming | `stream_csv()` calls `json.load()` in full before yielding any rows |
| Issue 10: Settings precedence unreproducible | `request_config` stores raw `req.max_pages` etc. (possibly `None`); re-runs lose original effective values |
| Issue 11: Settings validation permissive | `update_settings()` silently skips invalid keys; no Pydantic bounds enforcement on settings PUT |
| Issue 12: Cron fallback inaccurate | `_next_run_from_cron` falls back to `+1 day` silently if `croniter` throws; no validation at create time |
| Issue 13: Crawls table actions incomplete | `View Logs` and `More Options` buttons still have `display-only` CSS and `coming soon` titles in crawls.html |
| Issue 14: Frontend innerHTML injection | `crawls.html:558`, `app.js:147`, `reports.js:139,295,317,357`, `schedules.js:114` all inject untrusted strings via `innerHTML` |
| Issue 15: SSRF protection missing | No URL validation in `POST /api/crawl` or schedule creation for private IPs/loopback/schemes |
| Issue 16: Storage metrics follow symlinks | `_dir_size_mb()` in `server.py:597` uses `p.rglob("*")` without symlink check |
| Issue 17: Error contracts inconsistent | `GET /api/status/{task_id}` returns `{"error": "Task not found"}` with HTTP 200 |
| Issue 18: No automated tests | No `tests/` directory exists |

---

## Implementation Sequence (Strict Order — No Step Can Be Skipped)

```
Phase A: Backend Core Fixes       [Issues 6, 7, 10, 12, 15, 16, 17]
Phase B: Cancellation Completion  [Issues 1, 2, 3, 4, 5]
Phase C: Memory & Streaming Fixes [Issues 8, 9, 11]
Phase D: Frontend Completion      [Issues 13, 14]
Phase E: Automated Tests          [Issue 18]
```

---

## Phase A — Backend Core Fixes

### A1: Task-Specific Checkpoints *(Issue 6)*

**Problem**: All crawls write to `PROJECT_ROOT / "crawl_state.json"`. Concurrent crawls corrupt each other.

**Files to change**:
- [backend/paths.py](file:///d:/YT/Crawler/backend/paths.py)
- [backend/core/config.py](file:///d:/YT/Crawler/backend/core/config.py)
- [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — `_async_crawl_task()`
- [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — `POST /api/system/clear-checkpoint`

**Exact changes**:

**`backend/paths.py`** — Add checkpoint directory:
```python
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
```

**`backend/api/server.py`** — In `lifespan()`, create the directory:
```python
CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
```

**`backend/api/server.py`** — In `_async_crawl_task()`, replace the hardcoded `checkpoint_file`:
```python
# BEFORE (broken):
checkpoint_file=crawler_config.checkpoint_file,  # always root crawl_state.json

# AFTER (fixed):
from backend.paths import CHECKPOINTS_DIR
checkpoint_file=str(CHECKPOINTS_DIR / f"{task_id}.json"),
```

**`backend/api/server.py`** — In `_run_crawl_loop` (schedule checker), same fix:
```python
checkpoint_file=str(CHECKPOINTS_DIR / f"{task_id}.json"),
```

**`POST /api/system/clear-checkpoint`** — Change to clear the checkpoints directory or a specific task checkpoint:
```python
@app.delete("/api/system/checkpoints/{task_id}")
def clear_task_checkpoint(task_id: str):
    cp = CHECKPOINTS_DIR / f"{task_id}.json"
    if cp.exists():
        cp.unlink()
        return {"success": True}
    return {"success": False, "message": "Checkpoint not found"}

@app.post("/api/system/clear-checkpoint")
def clear_all_checkpoints():
    """Legacy: clear all checkpoint files."""
    if CHECKPOINTS_DIR.exists():
        import shutil
        shutil.rmtree(CHECKPOINTS_DIR)
        CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    return {"success": True}
```

**Acceptance criteria**: Two simultaneous crawls for different domains each produce their own `data/checkpoints/{task_id}.json` and do not interfere.

---

### A2: Atomic Report Publication *(Issue 7)*

**Problem**: `generate_markdown_report()` and `export_json()` write directly to `output/{domain}/`. A running read (download/detail) can see partial files.

**Files to change**:
- [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — `_async_crawl_task()` output section (lines 265–272)

**Exact change in `_async_crawl_task()`**:
```python
# BEFORE (broken — writes directly to live dir):
output_dir = OUTPUT_DIR / report.domain
output_dir.mkdir(parents=True, exist_ok=True)
md_path = generate_markdown_report(report, output_dir)
json_path = export_json(report, output_dir)
report_manager.generate_summary_json(report.domain, json_path)

# AFTER (atomic tmp-then-promote):
import shutil, uuid as _uuid

tmp_dir = OUTPUT_DIR / f"_tmp_{task_id}"
tmp_dir.mkdir(parents=True, exist_ok=True)
try:
    md_path = generate_markdown_report(report, tmp_dir)
    json_path = export_json(report, tmp_dir)
    report_manager.generate_summary_json(report.domain, json_path)

    # Atomically promote: remove old dir, rename tmp into place
    final_dir = OUTPUT_DIR / report.domain
    if final_dir.exists():
        shutil.rmtree(final_dir)
    tmp_dir.rename(final_dir)
except Exception:
    # Discard partial output on failure — never corrupt live reports
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    raise
```

Additionally, when a crawl is **cancelled** (the `if cancel_event is not None and cancel_event.is_set(): return` block), ensure any tmp dir is cleaned up:
```python
# In the Cancelled return path — after cancel check:
tmp_dir = OUTPUT_DIR / f"_tmp_{task_id}"
if tmp_dir.exists():
    shutil.rmtree(tmp_dir)
```

**Acceptance criteria**: Cancelling a crawl mid-run does not leave partial files in `output/{domain}/`. Completed crawls atomically replace the prior report.

---

### A3: Persist Complete Effective Config Snapshot *(Issue 10)*

**Problem**: `request_config` stored in task is `{max_pages: None, max_depth: None, ...}` (raw request values). Re-run uses `cfg.get("max_pages", 200)` and can therefore use **newer** user settings instead of the original effective values.

**Files to change**:
- [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — `POST /api/crawl` route
- [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — `_async_crawl_task()` (move resolution earlier)
- [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — `POST /api/tasks/{task_id}/rerun`

**Exact change in `POST /api/crawl`**:
```python
@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest, background_tasks: BackgroundTasks):
    # Resolve effective config NOW and save the snapshot — never save Nones
    crawler_defaults = settings_manager.get_effective_crawler_config()
    effective_config = {
        "max_pages": req.max_pages if req.max_pages is not None else crawler_defaults["max_pages"],
        "max_depth": req.max_depth if req.max_depth is not None else crawler_defaults["max_depth"],
        "max_time": req.max_time if req.max_time is not None else crawler_defaults["max_time"],
        "delay": req.delay if req.delay is not None else crawler_defaults["delay"],
        "concurrent": req.concurrent if req.concurrent is not None else crawler_defaults["concurrent"],
        "no_robots": req.no_robots if req.no_robots is not None else crawler_defaults["no_robots"],
        # NOTE: Do NOT persist jina_api_key — reference settings_manager at crawl time
    }
    target_domain = extract_base_domain(normalize_url(req.url))
    task_id = task_manager.create_task(
        req.url, request_config=effective_config, target_domain=target_domain
    )
    ...
```

**In `_async_crawl_task()`**: Replace the per-None resolution block with reading directly from `effective_config` stored on the task:
```python
stored_config = task_manager.get_task(task_id).get("request_config", {})
max_pages  = stored_config.get("max_pages", 200)
max_depth  = stored_config.get("max_depth", 5)
max_time   = stored_config.get("max_time", 30)
delay      = stored_config.get("delay", 1.5)
concurrent = stored_config.get("concurrent", 3)
no_robots  = stored_config.get("no_robots", False)
# Pipeline config still read fresh from settings (non-secret toggles)
saved = settings_manager.get_effective_pipeline_config()
```

**In `POST /api/tasks/{task_id}/rerun`**: Use the stored snapshot directly — no fallback to current settings:
```python
cfg = task_manager.rerun_task(task_id)  # returns effective_config dict
if not cfg:
    raise HTTPException(status_code=409, detail="Task has no stored config for re-run")
req = CrawlRequest(url=original["url"], **cfg)  # all values are concrete, no Nones
```

**Acceptance criteria**: A re-run task produces a `request_config` identical to the original task even if the user changed default settings between runs.

---

### A4: Enforce Strict Cron Validation *(Issue 12)*

**Problem**: `_next_run_from_cron()` silently falls back to `+1 day` when croniter throws. No validation at create/update time.

**Files to change**:
- [backend/services/schedule_manager.py](file:///d:/YT/Crawler/backend/services/schedule_manager.py)

**Exact changes**:

In `_next_run_from_cron()` — **remove silent fallback**:
```python
def _next_run_from_cron(cron_expr: str, base: datetime) -> datetime:
    if not _HAS_CRONITER:
        raise ValueError("croniter is required for custom cron schedules. Run: pip install croniter")
    try:
        itr = _croniter(cron_expr, base)
        return itr.get_next(datetime)
    except Exception as exc:
        raise ValueError(f"Invalid cron expression '{cron_expr}': {exc}") from exc
```

Add validation helper:
```python
def _validate_cron_expr(cron_expr: str) -> None:
    """Raise ValueError if cron_expr is invalid or croniter is missing."""
    if not _HAS_CRONITER:
        raise ValueError("croniter package is required for custom schedules. pip install croniter")
    try:
        _croniter(cron_expr, datetime.now())
    except Exception as exc:
        raise ValueError(f"Invalid cron expression '{cron_expr}': {exc}") from exc
```

In `create()` — call validation before saving:
```python
if frequency == "custom":
    if not cron_expr:
        raise ValueError("cron_expr is required for frequency='custom'")
    _validate_cron_expr(cron_expr)
```

In `update()` — same validation:
```python
if data.get("frequency") == "custom":
    ce = data.get("cron_expr") or sched.get("cron_expr", "")
    if not ce:
        raise ValueError("cron_expr is required for frequency='custom'")
    _validate_cron_expr(ce)
```

In [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — catch ValueError from `create()`/`update()` and return `422`:
```python
@app.post("/api/schedules")
def create_schedule(req: ScheduleCreateRequest):
    try:
        return schedule_manager.create(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

@app.put("/api/schedules/{sched_id}")
def update_schedule(sched_id: str, req: ScheduleCreateRequest):
    try:
        result = schedule_manager.update(sched_id, req.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return result
```

**Acceptance criteria**: `POST /api/schedules` with `frequency=custom` and an invalid `cron_expr` returns HTTP `422` with a descriptive error. No silent fallback to daily.

---

### A5: SSRF and Resource Abuse Protections *(Issue 15)*

**Problem**: `POST /api/crawl` and schedule create/update accept any URL, including `file://`, `ftp://`, `localhost`, `10.x.x.x`, `169.254.169.254` (cloud metadata endpoint), etc.

**Files to change**:
- **[NEW]** `backend/services/url_validator.py`
- [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — `POST /api/crawl`, `POST /api/schedules`, `PUT /api/schedules/{id}`

**Create `backend/services/url_validator.py`**:
```python
"""SSRF protection: validates user-supplied crawl URLs."""

import ipaddress
import re
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTS = {
    "localhost", "0.0.0.0",
}

def _is_private_or_loopback(host: str) -> bool:
    """Return True if host resolves to a private/loopback/link-local/reserved address."""
    try:
        addr = ipaddress.ip_address(host)
        return (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        )
    except ValueError:
        # Host is a name, not an IP — check known blocked hostnames
        return host.lower() in _BLOCKED_HOSTS


def validate_crawl_url(url: str) -> str:
    """Validate and normalise a URL for use as a crawl target.

    Raises ValueError with a descriptive message if the URL is blocked.
    Returns the normalised URL on success.
    """
    url = url.strip()
    if not url:
        raise ValueError("URL must not be empty")

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Unsupported scheme '{parsed.scheme}'. Only http and https are allowed."
        )

    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL must contain a valid host")

    if _is_private_or_loopback(host):
        raise ValueError(
            f"Crawling '{host}' is not allowed. "
            "Private, loopback, and link-local addresses are blocked."
        )

    return url
```

**In `POST /api/crawl`** — add URL validation before creating the task:
```python
from backend.services.url_validator import validate_crawl_url

@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest, background_tasks: BackgroundTasks):
    try:
        req.url = validate_crawl_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    ...
```

**In `POST /api/schedules`** — same validation:
```python
@app.post("/api/schedules")
def create_schedule(req: ScheduleCreateRequest):
    try:
        req.url = validate_crawl_url(req.url)
        return schedule_manager.create(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
```

**In `PUT /api/schedules/{sched_id}`** — validate URL if provided:
```python
if req.url:
    try:
        req.url = validate_crawl_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
```

**Acceptance criteria**: `POST /api/crawl` with `{"url": "http://169.254.169.254/"}` returns HTTP 422. `file://`, `ftp://`, `http://localhost` all rejected.

---

### A6: Fix Storage Metrics Symlink Traversal *(Issue 16)*

**Problem**: `_dir_size_mb()` in `server.py` uses `p.rglob("*")` and follows symlinks, which can measure files outside `output/` or `data/`.

**File to change**: [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — `GET /api/system/storage`

**Exact change**:
```python
# BEFORE:
total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

# AFTER (symlink-safe):
total = 0
for f in p.rglob("*"):
    if f.is_symlink():
        continue  # skip symlinks entirely
    if not f.is_file():
        continue
    try:
        # Verify the resolved file is still within the intended root
        f.resolve().relative_to(p.resolve())
        total += f.stat().st_size
    except (ValueError, OSError):
        continue  # skip anything that escapes the directory
```

**Acceptance criteria**: A symlink inside `output/` pointing outside the directory does not inflate the storage metric.

---

### A7: Fix Inconsistent Error Contracts *(Issue 17)*

**Problem**: `GET /api/status/{task_id}` returns `{"error": "Task not found"}` with HTTP 200 instead of 404.

**File to change**: [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py)

**Exact change** (lines 354–359):
```python
# BEFORE:
@app.get("/api/status/{task_id}")
def get_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        return {"error": "Task not found"}   # ← wrong: HTTP 200 with error body
    return task

# AFTER:
@app.get("/api/status/{task_id}")
def get_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
```

**Frontend impact**: Update any frontend code calling `/api/status/{id}` that checked for `data.error` to check `response.ok` instead.

**In `app.js`** — the fetch for status polling: ensure it handles 404 gracefully (the dashboard only polls `/api/tasks`, not `/api/status`, so this is safe to change without breaking the UI).

**Acceptance criteria**: `GET /api/status/nonexistent-id` returns HTTP 404 with `{"detail": "Task not found"}`.

---

## Phase B — Cancellation Lifecycle Completion

> **Key invariant**: A task must not show `Cancelled` status until its worker thread has **exited**. Until then, the API must return `CancellationRequested` and `202 Accepted`.

### B1: Bounded Cancellation in Every Pipeline Stage *(Issue 1)*

**Problem**: The `cancellation_event` is only checked at the top of the `_run_crawl_loop` iteration. A page fetch blocked inside a browser or HTTP stage can keep running indefinitely.

**Files to change**:
- [backend/crawler/orchestrator.py](file:///d:/YT/Crawler/backend/crawler/orchestrator.py) — `_run_crawl_loop()`
- [backend/crawler/orchestrator.py](file:///d:/YT/Crawler/backend/crawler/orchestrator.py) — `_process_page()` (must forward cancellation check to pipeline or abort early)
- [backend/core/config.py](file:///d:/YT/Crawler/backend/core/config.py) — Add `cancellation_timeout_seconds` to `CrawlerConfig`

**Exact changes**:

**In `_run_crawl_loop()` — cancel pending asyncio tasks when event is set**:
The existing code already calls `task.cancel()` on active tasks after loop exit. The gap is: the loop only checks the event at iteration start, so it might wait a full `asyncio.wait(timeout=remaining_time)` before noticing cancellation.

Change the `asyncio.wait` call to use a shorter timeout when cancellation is pending:
```python
# BEFORE:
done, active_tasks = await asyncio.wait(
    active_tasks,
    timeout=remaining_time,
    return_when=asyncio.FIRST_COMPLETED,
)

# AFTER:
# When cancellation is requested, use a tight poll interval so we notice quickly
check_interval = 1.0 if (cancellation_event and cancellation_event.is_set()) else min(remaining_time, 2.0)
done, active_tasks = await asyncio.wait(
    active_tasks,
    timeout=check_interval,
    return_when=asyncio.FIRST_COMPLETED,
)
```

**In `_process_page()` — add pre-fetch cancellation check**:
```python
async def _process_page(self, url: str, depth: int, cancellation_event=None) -> PageData | None:
    # Check cancellation before starting a potentially long fetch
    if cancellation_event is not None and cancellation_event.is_set():
        return None
    ...
    # After fetch — check again before expensive extraction
    if cancellation_event is not None and cancellation_event.is_set():
        return None
    ...
```

Update the `_process_entry` lambda in `_run_crawl_loop` to forward the event:
```python
async def _process_entry(e: QueueEntry = entry) -> PageData | None:
    await self._respect_rate_limit()
    return await self._process_page(e.url, e.depth, cancellation_event)
```

**Add grace-period config to `CrawlerConfig`** in [backend/core/config.py](file:///d:/YT/Crawler/backend/core/config.py):
```python
cancellation_grace_seconds: int = 30  # max time to wait for in-flight pages to finish
```

**In `_run_crawl_loop()`** — after cancellation break, wait for in-flight tasks with bounded grace period:
```python
if active_tasks and stop_reason == "cancelled":
    grace = getattr(self, 'cancellation_grace_seconds', 30)
    try:
        await asyncio.wait_for(
            asyncio.gather(*active_tasks, return_exceptions=True),
            timeout=grace
        )
    except asyncio.TimeoutError:
        # Force-cancel anything still running after grace period
        for task in active_tasks:
            task.cancel()
        await asyncio.gather(*active_tasks, return_exceptions=True)
```

**Acceptance criteria**: Cancellation request causes the crawler to stop fetching new pages within 2 seconds of the event being set. All in-flight fetches complete or timeout within `cancellation_grace_seconds`.

---

### B2: Worker-Exit-Gated Task Status *(Issue 2)*

**Problem**: `cancel_task()` immediately sets `status = "CancellationRequested"` (correct) but then `_async_crawl_task` sets `status = "Cancelled"` before the worker thread fully exits.

**Current flow problem**: `_async_crawl_task` runs inside `asyncio.to_thread()`. When it sets `status = "Cancelled"` and returns, the outer `_run_crawl_worker` then calls `cancellation_registry.remove()` and `worker_registry.remove()`. But the status is already `Cancelled` before cleanup — the API reports completion before resources are released.

**The fix is correct-by-construction** with the existing architecture — the final status update must be the last operation in `_run_crawl_worker`, not inside `_async_crawl_task`.

**File to change**: [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py)

**Refactor `_run_crawl_worker` and `_async_crawl_task`**:
```python
async def _run_crawl_worker(task_id: str, req: CrawlRequest):
    """Run crawl and set final Cancelled status AFTER cleanup — not before."""
    worker = asyncio.current_task()
    if worker is not None and worker_registry.get(task_id) is None:
        worker_registry.add(task_id, worker)
    try:
        # _async_crawl_task now signals COMPLETION or FAILURE but NOT cancellation status
        await asyncio.to_thread(background_crawl_task, task_id, req)
    finally:
        cancel_event = cancellation_registry.get(task_id)
        # Only now — after thread has exited — check if we should mark Cancelled
        if cancel_event is not None and cancel_event.is_set():
            task = task_manager.get_task(task_id)
            if task and task.get("status") == "CancellationRequested":
                task_manager.update_task(
                    task_id,
                    status="Cancelled",
                    cancelled_at=datetime.now().isoformat(),
                    completed_at=datetime.now().isoformat(),
                )
                if task.get("schedule_id"):
                    schedule_manager.update_last_run_status(task["schedule_id"], "Cancelled")
        # Cleanup registries
        cancellation_registry.remove(task_id)
        worker_registry.remove(task_id)
```

In `_async_crawl_task()` — remove all `status = "Cancelled"` assignments. When cancellation event is set, just `return` without setting any status (let `_run_crawl_worker.finally` handle it):
```python
# REPLACE all "Cancelled" status update paths in _async_crawl_task with:
if cancel_event is not None and cancel_event.is_set():
    return  # _run_crawl_worker.finally will handle the Cancelled transition
```

**Acceptance criteria**: After `POST /api/tasks/{id}/cancel` returns 202, subsequent `GET /api/tasks/{id}` returns `CancellationRequested` until the worker fully exits, then `Cancelled`. The status never briefly flips to `Cancelled` while the thread is still running.

---

### B3: Schedule Deletion Returns 202 While Crawl Is Pending *(Issue 3)*

**Problem**: `DELETE /api/schedules/{id}?cancel_active=true` signals active tasks and immediately deletes the schedule and returns HTTP 200. The crawl may still be running.

**File to change**: [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py)

**Exact change**:
```python
@app.delete("/api/schedules/{sched_id}", status_code=200)
def delete_schedule(sched_id: str, cancel_active: bool = Query(default=False)):
    active_tasks = task_manager.get_active_tasks_for_schedule(sched_id)
    if active_tasks and not cancel_active:
        raise HTTPException(
            status_code=409,
            detail="Schedule has an active crawl; use cancel_active=true to request cancellation",
        )
    
    cancellation_pending = False
    for task in active_tasks:
        task_manager.update_task(task["id"], status="CancellationRequested")
        cancellation_registry.request(task["id"])
        cancellation_pending = True
    
    deleted = schedule_manager.delete(sched_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    if cancellation_pending:
        # Return 202: schedule deleted, but active crawl(s) are still running
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "cancellation_pending": True,
                "message": "Schedule deleted. Active crawl(s) will stop within the grace period.",
                "active_task_ids": [t["id"] for t in active_tasks],
            }
        )
    return {"success": True, "cancellation_pending": False}
```

**Frontend update**: In `schedules.js` — handle `202` as a success with a toast: *"Schedule deleted. Active crawl is stopping..."*

**Acceptance criteria**: `DELETE /api/schedules/{id}?cancel_active=true` on a schedule with an active crawl returns HTTP 202 with `cancellation_pending: true`. The task status transitions to `Cancelled` only after the worker exits.

---

### B4: Atomic Schedule Claim (Eliminate Race) *(Issue 4)*

**Problem**: In `_schedule_checker_loop`, `get_due_schedules()` and `claim_schedule()` are called in two separate steps without a combined lock, allowing the scheduler to race with "Run Now".

**File to change**: [backend/services/schedule_manager.py](file:///d:/YT/Crawler/backend/services/schedule_manager.py)

**Add a new atomic method `claim_due_schedule()`**:
```python
def claim_due_schedule(self, sched_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    """Atomically re-verify a schedule is still due and eligible, then claim it.

    Unlike claim_schedule(), this method re-reads and re-checks eligibility
    within the same lock, preventing races between the periodic loop and run-now.

    Returns the claimed schedule dict, or None if the claim is rejected.
    """
    with self._store_lock:
        store = self._load()
        sched = store.get(sched_id)
        if not sched:
            return None
        if not sched.get("enabled"):
            return None
        # Re-check: is still due?
        now = _now_utc()
        next_run_str = sched.get("next_run_at", "")
        try:
            next_run = datetime.fromisoformat(next_run_str)
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=timezone.utc)
            if next_run > now:
                return None  # no longer due — lost the race
        except ValueError:
            return None
        # Check for overlapping active run
        # (caller should have verified task status, but double-check here)
        if sched.get("last_run_status") == "Running":
            return None  # previous run still in progress

        # Claim: advance next_run_at and record task
        next_run_after = _next_run_from_frequency(
            sched.get("frequency", "daily"),
            sched.get("cron_expr", ""),
            now,
        )
        sched["last_run_at"] = _iso(now)
        sched["last_task_id"] = task_id
        sched["next_run_at"] = _iso(next_run_after)
        sched["last_run_status"] = "Running"
        store[sched_id] = sched
        self._save(store)
    return sched
```

**In [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) — `_schedule_checker_loop()`**: Replace the two-step `get_due_schedules()` + `claim_schedule()` with the atomic version:
```python
# BEFORE:
due = schedule_manager.get_due_schedules()
for sched in due:
    ...
    task_id = task_manager.create_task(...)
    if schedule_manager.claim_schedule(sched["id"], task_id) is None:
        task_manager.delete_task(task_id)
        continue

# AFTER (atomic claim):
due = schedule_manager.get_due_schedules()
for sched in due:
    url = sched.get("url", "")
    if not url:
        continue
    # Pre-create task ID so we can use it in the atomic claim
    cfg = sched.get("config", {})
    req = CrawlRequest(url=url, ...)
    target_domain = extract_base_domain(normalize_url(url))
    task_id = task_manager.create_task(url, request_config=cfg, schedule_id=sched["id"], target_domain=target_domain)
    
    # Atomic claim: re-verify eligibility + advance next_run in one lock
    claimed = schedule_manager.claim_due_schedule(sched["id"], task_id)
    if claimed is None:
        task_manager.delete_task(task_id)  # claim rejected — delete orphan task
        continue
    ...
```

**Also update `POST /api/schedules/{id}/run-now`** to use `claim_due_schedule` or a simpler forced-claim that still uses the store lock to prevent overlap.

**Acceptance criteria**: Two concurrent `run-now` calls for the same schedule produce exactly one crawl task. The scheduler tick that fires while `run-now` is in progress does not spawn a duplicate.

---

### B5: Centralized Task Finalizer *(Issue 5)*

**Problem**: `_async_crawl_task()` has four separate finalization paths (completed, failed, cancelled-from-success, cancelled-from-exception) duplicating schedule status updates and timestamp logic. An exception in the cancellation branch can leave a schedule at `last_run_status = "Running"`.

**File to change**: [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py)

**Add a single finalizer function**:
```python
def _finalize_task(
    task_id: str,
    status: str,  # "Completed" | "Failed" | "CancellationRequested"
    *,
    report_domain: str = None,
    pages_crawled: int = None,
    duration_str: str = None,
    error: str = None,
):
    """Single authoritative point for task completion/failure recording."""
    now = datetime.now().isoformat()
    updates = {"completed_at": now}
    
    if status == "Completed":
        updates.update({
            "status": "Completed",
            "progress": 100,
            "error": None,
        })
        if report_domain: updates["report_domain"] = report_domain
        if pages_crawled is not None: updates["pages_crawled"] = pages_crawled
        if duration_str: updates["duration"] = duration_str
    elif status == "Failed":
        updates.update({"status": "Failed", "error": error or "Unknown error"})
    # "CancellationRequested" — leave to _run_crawl_worker.finally to finalize as Cancelled
    
    task_manager.update_task(task_id, **updates)
    
    # Update linked schedule regardless of outcome
    task = task_manager.get_task(task_id)
    if task and task.get("schedule_id"):
        schedule_manager.update_last_run_status(task["schedule_id"], status)
```

**Replace all scattered finalization calls in `_async_crawl_task()` with `_finalize_task()`**.

**Acceptance criteria**: A schedule's `last_run_status` is **never** left as `"Running"` after a crawl ends, regardless of whether it completed, failed, or was cancelled.

---

## Phase C — Memory, Streaming, and Validation Fixes

### C1: Make Report Detail Route Memory-Bounded *(Issue 8)*

**Problem**: `get_report_detail()` and `get_pages()` both call `json.load()` on the full 3MB+ `data.json` file every time.

**File to change**: [backend/services/report_manager.py](file:///d:/YT/Crawler/backend/services/report_manager.py)

**Fix `get_report_detail()`**: This route is used to show metadata + first N pages. Since `summary.json` already exists for new reports, read it for the metadata and then read only the pages slice:

```python
def get_report_detail(self, domain: str) -> Optional[Dict[str, Any]]:
    """Return summary metadata from summary.json + first 50 pages from data.json."""
    report_dir = self._validate_domain(domain)
    if report_dir is None:
        return None

    # Fast path: read metadata from summary.json
    summary = self._read_summary_fast(report_dir)
    if summary is None:
        summary = self._read_summary_compat(report_dir)

    data_file = report_dir / "data.json"
    if not data_file.exists():
        return None

    # Load only pages (bounded to first 50) via streaming approach
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)   # NOTE: still full load for now (see note below)
        pages = data.get("pages", [])
        # Include aggregated contact fields not in summary
        result = dict(summary)
        result["all_emails"] = data.get("all_emails", [])
        result["all_phones"] = data.get("all_phones", [])
        result["all_addresses"] = data.get("all_addresses", [])
        result["social_media"] = data.get("social_media", {})
        result["external_links"] = data.get("external_links", [])[:100]
        result["failed_urls"] = data.get("failed_urls", [])[:50]
        result["pages"] = pages[:50]
        result["total_page_count"] = len(pages)
    except Exception:
        return None

    return result
```

> **Note**: True streaming JSON parsing (e.g. `ijson`) requires an additional dependency. For files ≤10MB, `json.load()` is acceptable in this single-process app. If reports grow beyond 10MB, add `ijson` and parse the `pages` array incrementally. This is noted as a future improvement.

**Fix `get_pages()`**: This already limits the returned slice correctly. The only issue is loading the full file. For now, enforce a maximum `data.json` file size warning in the docstring. Add a file-size guard that returns an error if the file exceeds 50MB:
```python
if data_file.stat().st_size > 50 * 1024 * 1024:  # 50MB guard
    return {"domain": domain, "total": 0, "offset": 0, "limit": limit,
            "pages": [], "error": "Report file too large for in-memory pagination"}
```

**Acceptance criteria**: `GET /api/reports/{domain}` does not load more than 50 pages into the response body. `GET /api/reports/{domain}/pages` with `offset=0&limit=25` returns only 25 pages.

---

### C2: Make CSV Truly Stream Without Full Load *(Issue 9)*

**Problem**: `stream_csv()` calls `json.load()` which fully buffers the 3MB+ file before the first row is yielded to the client.

**File to change**: [backend/services/report_manager.py](file:///d:/YT/Crawler/backend/services/report_manager.py)

**Fix `stream_csv()` generator**:
```python
def stream_csv(self, domain: str) -> Optional[Iterator[str]]:
    """Stream CSV rows for all pages without holding the full file in memory."""
    report_dir = self._validate_domain(domain)
    if report_dir is None:
        return None

    data_file = report_dir / "data.json"
    if not data_file.exists():
        return None

    def _generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["url", "page_title", "depth", "word_count",
                         "emails", "phones", "images_count"])
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        # Use ijson for incremental parsing if available; else fall back to full load
        try:
            import ijson  # type: ignore
            with open(data_file, "rb") as f:
                for page in ijson.items(f, "pages.item"):
                    _write_page_row(writer, output, page)
                    yield output.getvalue()
                    output.truncate(0)
                    output.seek(0)
        except ImportError:
            # Fallback: full load — acceptable for files under ~10MB
            try:
                with open(data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                return
            for page in data.get("pages", []):
                _write_page_row(writer, output, page)
                yield output.getvalue()
                output.truncate(0)
                output.seek(0)

    def _write_page_row(writer, output, page):
        def safe(val):
            val = str(val)
            return ("'" + val) if val.startswith(("=", "+", "-", "@")) else val
        writer.writerow([
            safe(page.get("url", "")),
            safe(page.get("page_title", "")),
            page.get("depth", 0),
            page.get("word_count", 0),
            "; ".join(page.get("emails", [])),
            "; ".join(page.get("phones", [])),
            len(page.get("images", [])),
        ])

    return _generate()
```

Add `ijson` to [requirements.txt](file:///d:/YT/Crawler/requirements.txt) as an optional dependency:
```
ijson>=3.2.3
```

**Acceptance criteria**: `GET /api/reports/{domain}/download?format=csv` begins streaming the first row within 1 second for a 3MB report, without loading the whole file into memory first (when `ijson` is installed).

---

### C3: Strict Settings Validation with Pydantic Bounds *(Issue 11)*

**Problem**: `SettingsUpdateRequest` has all `Optional` fields with no bounds. Invalid values like `max_pages=-1` or `delay="yes"` are silently skipped.

**File to change**: [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py)

**Replace `SettingsUpdateRequest` with a bounded model**:
```python
class SettingsUpdateRequest(BaseModel):
    max_pages:        Optional[int]   = Field(default=None, ge=1, le=5000)
    max_depth:        Optional[int]   = Field(default=None, ge=1, le=20)
    concurrent:       Optional[int]   = Field(default=None, ge=1, le=20)
    delay:            Optional[float] = Field(default=None, ge=0.0, le=60.0)
    max_time:         Optional[int]   = Field(default=None, ge=1, le=1440)
    respect_robots:   Optional[bool]  = None
    enable_crawl4ai:  Optional[bool]  = None
    enable_scrapling: Optional[bool]  = None
    enable_jina:      Optional[bool]  = None
    enable_curl_tls:  Optional[bool]  = None
    enable_curl_rotated: Optional[bool] = None
    enable_httpx:     Optional[bool]  = None
    jina_api_key:     Optional[str]   = Field(default=None, max_length=500)
    theme:            Optional[str]   = Field(default=None, pattern="^(light|dark|system)$")
    polling_interval: Optional[int]   = Field(default=None, ge=0, le=60)
```

Pydantic will now automatically return HTTP 422 with field-level error details when bounds are violated.

Also fix `update_settings()` in [backend/services/settings_manager.py](file:///d:/YT/Crawler/backend/services/settings_manager.py) to remove the `pass` on invalid values — values arriving here are already validated by Pydantic, so the silent skip is redundant. Remove it and let proper types flow through:
```python
# Remove the try/except with pass — replace with direct assignment:
raw[key] = value
```

**Acceptance criteria**: `PUT /api/settings` with `{"max_pages": -5}` returns HTTP 422. `{"theme": "purple"}` returns HTTP 422. Valid settings persist correctly.

---

## Phase D — Frontend Completion

### D1: Implement Crawls Table Actions *(Issue 13)*

**Problem**: "View Logs" and "More Options" buttons in [crawls.html](file:///d:/YT/Crawler/frontend/crawls.html) are styled `display-only` with `title="(coming soon)"` — they do nothing.

**File to change**: [frontend/crawls.html](file:///d:/YT/Crawler/frontend/crawls.html) — the `tr.innerHTML` template (lines 558+)

**Replace the two `display-only` buttons** with functional implementations:

**"View Logs" → Task Detail Drawer**:
- Remove `display-only` class and `coming soon` title.
- On click, `GET /api/tasks/{id}` and show a slide-over drawer with:
  - URL, Status, Started At, Completed At, Duration
  - Pages Crawled, Error (if failed), Report Domain link
  - Schedule ID (if scheduled run)
  - Link to report: `<a href="/reports?domain={report_domain}">View Report</a>`

**"More Options" → Action Popover**:
- Remove `display-only` class and `coming soon` title.
- On click, show a small popover/dropdown with context-sensitive actions:

| Task Status | Available Actions |
|---|---|
| Running / Queued | Cancel |
| CancellationRequested | (greyed out: Cancelling…) |
| Completed | View Report, Re-run, Delete |
| Failed | Re-run, Delete |
| Cancelled | Re-run, Delete |

**Implementation in `crawls.html` script section**:
```javascript
// Task Detail Drawer
async function openTaskDrawer(taskId) {
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
    if (!res.ok) { alert('Task not found'); return; }
    const task = await res.json();
    
    // Populate drawer using textContent (not innerHTML) for untrusted fields
    document.getElementById('drawer-url').textContent       = task.url;
    document.getElementById('drawer-status').textContent    = task.status;
    document.getElementById('drawer-started').textContent   = task.started_at || '—';
    document.getElementById('drawer-completed').textContent = task.completed_at || '—';
    document.getElementById('drawer-duration').textContent  = task.duration || '—';
    document.getElementById('drawer-pages').textContent     = task.pages_crawled || 0;
    document.getElementById('drawer-error').textContent     = task.error || 'None';
    
    const reportLink = document.getElementById('drawer-report-link');
    if (task.report_domain) {
        reportLink.href = `/reports?domain=${encodeURIComponent(task.report_domain)}`;
        reportLink.style.display = 'inline';
    } else {
        reportLink.style.display = 'none';
    }
    document.getElementById('task-drawer').classList.add('open');
}

// Re-run action
async function rerunTask(taskId) {
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/rerun`, {method: 'POST'});
    if (!res.ok) { alert('Re-run failed'); return; }
    const data = await res.json();
    alert(`New crawl started: ${data.task_id}`);
    await fetchAndRender();
}

// Delete task
async function deleteTask(taskId) {
    if (!confirm('Delete this task record? This cannot be undone.')) return;
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`, {method: 'DELETE'});
    if (!res.ok) {
        const err = await res.json();
        alert(err.detail || 'Delete failed');
        return;
    }
    await fetchAndRender();
}
```

Add the drawer HTML element above `</div>` in the body:
```html
<div id="task-drawer" class="side-drawer">
    <div class="drawer-header">
        <h3>Task Details</h3>
        <button onclick="document.getElementById('task-drawer').classList.remove('open')">✕</button>
    </div>
    <div class="drawer-body">
        <div class="detail-row"><label>URL</label><span id="drawer-url"></span></div>
        <div class="detail-row"><label>Status</label><span id="drawer-status"></span></div>
        <div class="detail-row"><label>Started</label><span id="drawer-started"></span></div>
        <div class="detail-row"><label>Completed</label><span id="drawer-completed"></span></div>
        <div class="detail-row"><label>Duration</label><span id="drawer-duration"></span></div>
        <div class="detail-row"><label>Pages</label><span id="drawer-pages"></span></div>
        <div class="detail-row"><label>Error</label><span id="drawer-error"></span></div>
        <div class="detail-row"><label>Report</label><a id="drawer-report-link" href="#">View Report →</a></div>
    </div>
</div>
```

Add drawer CSS to [frontend/styles.css](file:///d:/YT/Crawler/frontend/styles.css):
```css
.side-drawer {
    position: fixed; top: 0; right: -420px; width: 420px; height: 100vh;
    background: var(--card-bg); border-left: 1px solid var(--border-color);
    box-shadow: var(--shadow-lg); transition: right 0.25s ease; z-index: 200;
    display: flex; flex-direction: column;
}
.side-drawer.open { right: 0; }
.drawer-header { display: flex; justify-content: space-between; padding: 20px 24px;
                 border-bottom: 1px solid var(--border-color); }
.drawer-body { padding: 24px; overflow-y: auto; flex: 1; }
.detail-row { display: flex; flex-direction: column; margin-bottom: 16px; }
.detail-row label { font-size: 11px; font-weight: 600; color: var(--text-muted); 
                    text-transform: uppercase; margin-bottom: 4px; }
.detail-row span { font-size: 14px; color: var(--text-main); word-break: break-all; }
```

**Acceptance criteria**: Clicking the "View Logs" icon opens the drawer with task details. Clicking "More Options" shows the context menu. Re-run, Delete, and Cancel all work correctly.

---

### D2: Eliminate innerHTML XSS Risk *(Issue 14)*

**Problem**: Crawler-derived values (URLs, page titles, error messages, contact data) are interpolated via template literals into `innerHTML`, which can execute arbitrary HTML/JavaScript from a malicious crawled page.

**Files to change**:
- [frontend/crawls.html](file:///d:/YT/Crawler/frontend/crawls.html) — `tr.innerHTML` template (line 558)
- [frontend/app.js](file:///d:/YT/Crawler/frontend/app.js) — `tr.innerHTML` template (line 147)
- [frontend/reports.js](file:///d:/YT/Crawler/frontend/reports.js) — multiple `innerHTML` templates
- [frontend/schedules.js](file:///d:/YT/Crawler/frontend/schedules.js) — `tr.innerHTML` template (line 114)

**Strategy**: Add a shared `escapeHtml()` utility and use `textContent` for all untrusted string values. Only use `innerHTML` for trusted static markup (no user data embedded).

**Add to a shared script or inline in every affected page**:
```javascript
/**
 * Escape untrusted string for safe insertion into HTML attributes or textContent.
 * For textContent assignments, just assign directly — no escaping needed.
 * For innerHTML template literals, call this on every untrusted value.
 */
function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = String(str ?? '');
    return d.innerHTML;  // browser escapes <, >, ", ', &
}

/**
 * Validate that a URL uses only http or https before using it in href.
 * Returns '#' for any other scheme.
 */
function safeHref(url) {
    try {
        const parsed = new URL(String(url ?? ''));
        return (parsed.protocol === 'http:' || parsed.protocol === 'https:') ? url : '#';
    } catch { return '#'; }
}
```

**In `crawls.html` `tr.innerHTML` template** — wrap every task-derived value:
```javascript
// BEFORE:
<span class="domain-name">${hostname}</span>
<span class="domain-url" title="${task.url}">${fullUrl}</span>
${task.status}

// AFTER:
<span class="domain-name">${escapeHtml(hostname)}</span>
<span class="domain-url" title="${escapeHtml(task.url)}">${escapeHtml(fullUrl)}</span>
${escapeHtml(task.status)}
```

**Better alternative for complex rows** — build the row with DOM API instead of `innerHTML`:
```javascript
const tr = document.createElement('tr');
// Domain cell
const domainName = document.createElement('span');
domainName.className = 'domain-name';
domainName.textContent = hostname;  // textContent = inherently safe
// ... attach all cells via DOM API
tbody.appendChild(tr);
```

**For `reports.js` contact lists** — use DOM API:
```javascript
// BEFORE:
container.innerHTML += `<span class="contact-item">${email}</span>`;

// AFTER:
const span = document.createElement('span');
span.className = 'contact-item';
span.textContent = email;  // safe — no HTML injection possible
container.appendChild(span);
```

**For link (`href`) attributes** — always use `safeHref()`:
```javascript
const a = document.createElement('a');
a.href = safeHref(page.url);
a.textContent = page.url;  // safe
```

**Acceptance criteria**: A crawl result containing `<script>alert(1)</script>` in a page title, URL, or email address is displayed as literal text in the UI — no script executes.

---

## Phase E — Automated Tests

### E1: Create Test Suite *(Issue 18)*

**Problem**: No automated tests exist to verify cancellation, security, concurrency, or recovery behavior.

**Create directory structure**:
```
tests/
├── conftest.py               # Shared fixtures: tmp data/output dirs, test client
├── test_cancellation.py      # Issues 1, 2, 3, 5
├── test_schedule_claims.py   # Issue 4
├── test_checkpoints.py       # Issue 6
├── test_report_publication.py# Issue 7
├── test_report_memory.py     # Issues 8, 9
├── test_settings.py          # Issues 10, 11
├── test_schedule_cron.py     # Issue 12
├── test_security.py          # Issues 14, 15, 16, 17
└── test_task_api.py          # Issue 13 (API side)
```

**`conftest.py`** — Isolated fixtures:
```python
import pytest, tempfile, shutil
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path, monkeypatch):
    """Redirect all storage paths to a temporary directory per test."""
    import backend.paths as paths
    data = tmp_path / "data"
    output = tmp_path / "output"
    data.mkdir(); output.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "OUTPUT_DIR", output)
    monkeypatch.setattr(paths, "TASKS_FILE", data / "tasks.json")
    monkeypatch.setattr(paths, "SCHEDULES_FILE", data / "schedules.json")
    monkeypatch.setattr(paths, "SETTINGS_FILE", data / "settings.json")
    monkeypatch.setattr(paths, "CHECKPOINTS_DIR", data / "checkpoints")
    yield
```

**Key test cases per issue**:

**`test_security.py`**:
```python
def test_ssrf_loopback_rejected(client):
    r = client.post("/api/crawl", json={"url": "http://127.0.0.1/secret"})
    assert r.status_code == 422

def test_ssrf_private_ip_rejected(client):
    r = client.post("/api/crawl", json={"url": "http://192.168.1.1/"})
    assert r.status_code == 422

def test_ssrf_metadata_endpoint_rejected(client):
    r = client.post("/api/crawl", json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 422

def test_file_scheme_rejected(client):
    r = client.post("/api/crawl", json={"url": "file:///etc/passwd"})
    assert r.status_code == 422

def test_path_traversal_report_rejected(client, tmp_output):
    r = client.get("/api/reports/..%2F..%2Fetc")
    assert r.status_code in (400, 404)

def test_status_returns_404_not_200_error(client):
    r = client.get("/api/status/nonexistent-task-id")
    assert r.status_code == 404  # Issue 17: was returning 200

def test_symlink_excluded_from_storage_metric(client, tmp_output):
    # Create a symlink in output/ pointing to /etc
    target = tmp_output / "link_dir"
    target.symlink_to("/etc")
    r = client.get("/api/system/storage")
    assert r.status_code == 200
    # Size should not include files under /etc
    assert r.json()["output_size_mb"] < 1.0
```

**`test_settings.py`**:
```python
def test_bounds_rejected(client):
    r = client.put("/api/settings", json={"max_pages": -1})
    assert r.status_code == 422

def test_invalid_theme_rejected(client):
    r = client.put("/api/settings", json={"theme": "rainbow"})
    assert r.status_code == 422

def test_jina_key_not_returned_raw(client):
    client.put("/api/settings", json={"jina_api_key": "sk-real-secret"})
    r = client.get("/api/settings")
    assert "sk-real-secret" not in str(r.json())
    assert r.json()["has_jina_key"] is True
```

**`test_schedule_cron.py`**:
```python
def test_invalid_cron_rejected(client):
    r = client.post("/api/schedules", json={
        "url": "https://example.com",
        "frequency": "custom",
        "cron_expr": "not-a-valid-cron"
    })
    assert r.status_code == 422

def test_custom_without_cron_rejected(client):
    r = client.post("/api/schedules", json={
        "url": "https://example.com",
        "frequency": "custom",
        "cron_expr": ""
    })
    assert r.status_code == 422
```

**`test_cancellation.py`** (integration — requires mock orchestrator):
```python
def test_cancel_returns_202_not_200(client, created_running_task):
    r = client.post(f"/api/tasks/{created_running_task}/cancel")
    assert r.status_code == 202
    assert r.json()["status"] == "CancellationRequested"

def test_task_not_cancelled_while_worker_running(client, created_running_task):
    client.post(f"/api/tasks/{created_running_task}/cancel")
    r = client.get(f"/api/tasks/{created_running_task}")
    # Must remain CancellationRequested until worker exits
    assert r.json()["status"] == "CancellationRequested"
```

**Add to requirements.txt** (test dependencies):
```
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27
```

**Acceptance criteria**: `pytest tests/ -v` passes with 0 failures. Every acceptance criterion from Phases A–D is verified by at least one automated test.

---

## Complete File Change Map

| File | Change | Issues Addressed |
|---|---|---|
| [backend/paths.py](file:///d:/YT/Crawler/backend/paths.py) | Add `CHECKPOINTS_DIR` | A1/6 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Task-specific checkpoint path | A1/6 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Atomic tmp-then-promote report publication | A2/7 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Store resolved effective config (not Nones) | A3/10 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Re-run uses stored snapshot | A3/10 |
| [backend/services/schedule_manager.py](file:///d:/YT/Crawler/backend/services/schedule_manager.py) | Validate & require cron; remove silent fallback | A4/12 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Raise 422 on cron ValueError | A4/12 |
| **[NEW]** `backend/services/url_validator.py` | SSRF protection: scheme + IP validation | A5/15 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Call `validate_crawl_url()` in crawl + schedule routes | A5/15 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Symlink-safe `_dir_size_mb()` | A6/16 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | `/api/status/{id}` returns 404 not 200 | A7/17 |
| [backend/crawler/orchestrator.py](file:///d:/YT/Crawler/backend/crawler/orchestrator.py) | Short poll interval when cancellation pending; forward event to `_process_page`; grace-period gather | B1/1 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | `_run_crawl_worker.finally` sets `Cancelled` after thread exits | B2/2 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Remove `Cancelled` status sets from `_async_crawl_task` | B2/2 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | `DELETE /api/schedules/{id}` returns 202 when cancellation pending | B3/3 |
| [frontend/schedules.js](file:///d:/YT/Crawler/frontend/schedules.js) | Handle 202 response for schedule deletion | B3/3 |
| [backend/services/schedule_manager.py](file:///d:/YT/Crawler/backend/services/schedule_manager.py) | Add `claim_due_schedule()` atomic method | B4/4 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Replace 2-step claim with `claim_due_schedule()` in scheduler loop and run-now | B4/4 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Add `_finalize_task()` centralizer; replace all scattered finalization | B5/5 |
| [backend/services/report_manager.py](file:///d:/YT/Crawler/backend/services/report_manager.py) | Bounded `get_report_detail()`; file-size guard in `get_pages()` | C1/8 |
| [backend/services/report_manager.py](file:///d:/YT/Crawler/backend/services/report_manager.py) | `stream_csv()` uses `ijson` if available | C2/9 |
| [requirements.txt](file:///d:/YT/Crawler/requirements.txt) | Add `ijson>=3.2.3` | C2/9 |
| [backend/api/server.py](file:///d:/YT/Crawler/backend/api/server.py) | Pydantic bounds on `SettingsUpdateRequest` | C3/11 |
| [backend/services/settings_manager.py](file:///d:/YT/Crawler/backend/services/settings_manager.py) | Remove silent `pass` on invalid values | C3/11 |
| [frontend/crawls.html](file:///d:/YT/Crawler/frontend/crawls.html) | Implement "View Logs" drawer + "More Options" popover | D1/13 |
| [frontend/styles.css](file:///d:/YT/Crawler/frontend/styles.css) | Add `.side-drawer` styles | D1/13 |
| [frontend/crawls.html](file:///d:/YT/Crawler/frontend/crawls.html) | Replace `innerHTML` template with `escapeHtml()` or DOM API | D2/14 |
| [frontend/app.js](file:///d:/YT/Crawler/frontend/app.js) | Replace `innerHTML` template with `escapeHtml()` or DOM API | D2/14 |
| [frontend/reports.js](file:///d:/YT/Crawler/frontend/reports.js) | Replace `innerHTML` with DOM API for contact/page data | D2/14 |
| [frontend/schedules.js](file:///d:/YT/Crawler/frontend/schedules.js) | Replace `innerHTML` template with `escapeHtml()` or DOM API | D2/14 |
| **[NEW]** `tests/conftest.py` | Isolated tmp fixtures | E1/18 |
| **[NEW]** `tests/test_security.py` | SSRF, traversal, symlink, error contract tests | E1/18 |
| **[NEW]** `tests/test_settings.py` | Bounds, masking, precedence tests | E1/18 |
| **[NEW]** `tests/test_schedule_cron.py` | Cron validation tests | E1/18 |
| **[NEW]** `tests/test_cancellation.py` | Cancellation lifecycle tests | E1/18 |
| **[NEW]** `tests/test_schedule_claims.py` | Duplicate claim prevention tests | E1/18 |
| **[NEW]** `tests/test_checkpoints.py` | Task-specific checkpoint isolation tests | E1/18 |
| **[NEW]** `tests/test_report_publication.py` | Atomic publication / cancellation cleanup tests | E1/18 |
| **[NEW]** `tests/test_task_api.py` | Task detail, re-run, delete API tests | E1/18 |

---

## Final Acceptance Checklist

Every item below must be true before this implementation is considered complete:

- [ ] Cancelling a task immediately returns 202 with `CancellationRequested`
- [ ] Crawler stops fetching new pages within 2 seconds of cancellation event being set
- [ ] In-flight page fetches complete or timeout within `cancellation_grace_seconds` (default: 30)
- [ ] Task status shows `CancellationRequested` until the worker thread exits, then `Cancelled`
- [ ] Deleting a schedule with an active crawl returns 202 with `cancellation_pending: true`
- [ ] Two concurrent "run-now" calls for the same schedule produce exactly one task
- [ ] Schedule `last_run_status` is never left as `"Running"` after crawl ends
- [ ] Two concurrent crawls produce separate checkpoint files in `data/checkpoints/`
- [ ] Cancelling a crawl does not leave partial files in `output/{domain}/`
- [ ] Re-run task uses exactly the same effective config as the original — not current settings
- [ ] `POST /api/crawl` with `http://localhost` returns 422
- [ ] `GET /api/status/nonexistent` returns 404, not 200
- [ ] A symlink in `output/` pointing outside the directory is ignored in storage metrics
- [ ] `PUT /api/settings` with out-of-bounds values returns 422 with field-level errors
- [ ] Invalid cron expression returns 422 at schedule create/update time
- [ ] Crawled `<script>alert(1)</script>` in page title/URL renders as literal text in UI
- [ ] `View Logs` drawer opens with task details for any completed/failed/cancelled task
- [ ] `More Options` popover shows correct actions based on task status
- [ ] `pytest tests/ -v` passes with 0 failures across all test files
