import sys
import asyncio
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Fix for Playwright/asyncio NotImplementedError on Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from backend.services.task_manager import task_manager
from backend.services.settings_manager import settings_manager
from backend.services.schedule_manager import schedule_manager
from backend.services.report_manager import report_manager
from backend.services.cancellation import (
    cancellation_registry,
    worker_registry,
)
from backend.core.config import PipelineConfig, CrawlerConfig
from backend.crawler.orchestrator import CrawlOrchestrator
from backend.exporters.markdown_report import generate_markdown_report
from backend.exporters.json_export import export_json
from backend.paths import FRONTEND_DIR, OUTPUT_DIR, DATA_DIR, CHECKPOINT_FILE
from backend.discovery.url_utils import extract_base_domain, normalize_url


# ── Lifespan (startup / shutdown + scheduler loop) ────────────────────────────

async def _schedule_checker_loop(stop_event: asyncio.Event):
    """Check for due schedules every 60 seconds."""
    while not stop_event.is_set():
        try:
            due = schedule_manager.get_due_schedules()
            for sched in due:
                url = sched.get("url", "")
                if not url:
                    continue
                cfg = sched.get("config", {})
                req = CrawlRequest(
                    url=url,
                    max_pages=cfg.get("max_pages", 200),
                    max_depth=cfg.get("max_depth", 5),
                    max_time=cfg.get("max_time", 30),
                    delay=cfg.get("delay", 1.5),
                    concurrent=cfg.get("concurrent", 3),
                    no_robots=cfg.get("no_robots", False),
                )
                target_domain = extract_base_domain(normalize_url(url))
                task_id = task_manager.create_task(
                    url,
                    request_config=cfg,
                    schedule_id=sched["id"],
                    target_domain=target_domain,
                )
                if schedule_manager.claim_schedule(sched["id"], task_id) is None:
                    task_manager.delete_task(task_id)
                    continue
                # Dispatch off the event loop and retain the task until it exits.
                cancellation_registry.register(task_id)
                worker = asyncio.create_task(_run_crawl_worker(task_id, req))
                worker_registry.add(task_id, worker)
                worker.add_done_callback(lambda _, tid=task_id: worker_registry.remove(tid))
        except Exception:
            pass  # keep the loop alive on any error

        # Wait up to 60 s, but wake early on shutdown
        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()), timeout=60.0
            )
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(_schedule_checker_loop(stop_event))

    yield  # ── application runs here ──

    # Shutdown
    stop_event.set()
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    # Request cancellation and wait briefly for all active workers to finish.
    for task_id in worker_registry.active_ids():
        cancellation_registry.request(task_id)
    for task_id in worker_registry.active_ids():
        await worker_registry.cancel_and_wait(task_id, timeout=10.0)


# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(title="CrawlMaster API", lifespan=lifespan)

# Ensure static files directory exists (may run before lifespan on some setups)
FRONTEND_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Mount static files (HTML, CSS, JS, assets)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Request / response models ─────────────────────────────────────────────────

class CrawlRequest(BaseModel):
    url: str
    max_pages: Optional[int] = Field(default=None, ge=1, le=5000)
    max_depth: Optional[int] = Field(default=None, ge=1, le=20)
    max_time: Optional[int] = Field(default=None, ge=1, le=1440)
    delay: Optional[float] = Field(default=None, ge=0.0, le=60.0)
    concurrent: Optional[int] = Field(default=None, ge=1, le=20)
    no_robots: Optional[bool] = None


class ScheduleCreateRequest(BaseModel):
    name: str = ""
    url: str
    frequency: str = "daily"
    cron_expr: str = ""
    config: dict = Field(default_factory=dict)
    timezone: str = "UTC"


class SettingsUpdateRequest(BaseModel):
    max_pages: Optional[int] = None
    max_depth: Optional[int] = None
    concurrent: Optional[int] = None
    delay: Optional[float] = None
    max_time: Optional[int] = None
    respect_robots: Optional[bool] = None
    enable_crawl4ai: Optional[bool] = None
    enable_scrapling: Optional[bool] = None
    enable_jina: Optional[bool] = None
    enable_curl_tls: Optional[bool] = None
    enable_curl_rotated: Optional[bool] = None
    enable_httpx: Optional[bool] = None
    jina_api_key: Optional[str] = None
    theme: Optional[str] = None
    polling_interval: Optional[int] = None


# ── Crawl background task ─────────────────────────────────────────────────────

def background_crawl_task(task_id: str, req: CrawlRequest):
    """Sync wrapper — runs the async crawl in a fresh event loop on a worker thread."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(_async_crawl_task(task_id, req))


async def _run_crawl_worker(task_id: str, req: CrawlRequest):
    """Run a blocking-thread crawl and keep it visible to cancellation/shutdown."""
    worker = asyncio.current_task()
    if worker is not None and worker_registry.get(task_id) is None:
        worker_registry.add(task_id, worker)
    try:
        await asyncio.to_thread(background_crawl_task, task_id, req)
    finally:
        cancellation_registry.remove(task_id)
        worker_registry.remove(task_id)


async def _async_crawl_task(task_id: str, req: CrawlRequest):
    cancel_event = cancellation_registry.get(task_id)
    try:
        if cancel_event is not None and cancel_event.is_set():
            task_manager.update_task(
                task_id,
                status="Cancelled",
                cancelled_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat(),
            )
            return
        task_manager.update_task(task_id, status="Running")

        # Resolve effective config: saved settings < request overrides
        crawler_defaults = settings_manager.get_effective_crawler_config()
        max_pages = req.max_pages if req.max_pages is not None else crawler_defaults["max_pages"]
        max_depth = req.max_depth if req.max_depth is not None else crawler_defaults["max_depth"]
        max_time = req.max_time if req.max_time is not None else crawler_defaults["max_time"]
        delay = req.delay if req.delay is not None else crawler_defaults["delay"]
        concurrent = req.concurrent if req.concurrent is not None else crawler_defaults["concurrent"]
        no_robots = req.no_robots if req.no_robots is not None else crawler_defaults["no_robots"]
        saved = settings_manager.get_effective_pipeline_config()
        pipeline_config = PipelineConfig(
            timeout=saved.get("timeout", 30),
            enable_crawl4ai=saved.get("enable_crawl4ai", True),
            enable_scrapling=saved.get("enable_scrapling", True),
            enable_jina=saved.get("enable_jina", True),
            enable_curl_tls=saved.get("enable_curl_tls", True),
            enable_curl_rotated=saved.get("enable_curl_rotated", True),
            enable_httpx=saved.get("enable_httpx", True),
            jina_api_key=saved.get("jina_api_key", ""),
        )

        crawler_config = CrawlerConfig(
            max_pages=max_pages,
            max_discovered_urls=1000,
            max_depth=max_depth,
            max_time_minutes=max_time,
            request_delay=delay,
            max_concurrent=concurrent,
            respect_robots=not no_robots,
            pipeline=pipeline_config,
        )

        orchestrator = CrawlOrchestrator(
            max_pages=crawler_config.max_pages,
            max_discovered_urls=crawler_config.max_discovered_urls,
            max_depth=crawler_config.max_depth,
            max_time_minutes=crawler_config.max_time_minutes,
            request_delay=crawler_config.request_delay,
            max_concurrent=crawler_config.max_concurrent,
            respect_robots=crawler_config.respect_robots,
            checkpoint_every=crawler_config.checkpoint_every,
            checkpoint_file=crawler_config.checkpoint_file,
            pipeline_config=pipeline_config,
        )

        start_time = datetime.now()

        def progress_callback(completed, total, current_url):
            progress_pct = int((completed / total) * 100) if total > 0 else 0
            task_manager.update_task(
                task_id, progress=progress_pct, pages_crawled=completed
            )

        report = await orchestrator.crawl(
            url=req.url,
            resume=False,
            progress_callback=progress_callback,
            cancellation_event=cancel_event,
        )

        if cancel_event is not None and cancel_event.is_set():
            task_manager.update_task(
                task_id,
                status="Cancelled",
                cancelled_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat(),
                error=None,
            )
            task = task_manager.get_task(task_id) or {}
            if task.get("schedule_id"):
                schedule_manager.update_last_run_status(task["schedule_id"], "Cancelled")
            return

        # Generate outputs atomically into a tmp dir then promote
        output_dir = OUTPUT_DIR / report.domain
        output_dir.mkdir(parents=True, exist_ok=True)
        md_path = generate_markdown_report(report, output_dir)
        json_path = export_json(report, output_dir)

        # Generate lightweight summary.json for fast listing
        report_manager.generate_summary_json(report.domain, json_path)

        end_time = datetime.now()
        duration_secs = int((end_time - start_time).total_seconds())
        mins, secs = divmod(duration_secs, 60)
        duration_str = f"{mins:02d}:{secs:02d}"

        task_manager.update_task(
            task_id,
            status="Completed",
            progress=100,
            pages_crawled=report.total_pages_crawled,
            completed_at=end_time.isoformat(),
            duration=duration_str,
            report_domain=report.domain,
        )
        task = task_manager.get_task(task_id) or {}
        if task.get("schedule_id"):
            schedule_manager.update_last_run_status(task["schedule_id"], "Completed")

    except Exception as e:
        if cancel_event is not None and cancel_event.is_set():
            task_manager.update_task(
                task_id,
                status="Cancelled",
                cancelled_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat(),
                error=None,
            )
            return
        task_manager.update_task(
            task_id,
            status="Failed",
            error=str(e),
            completed_at=datetime.now().isoformat(),
        )
        task = task_manager.get_task(task_id) or {}
        if task.get("schedule_id"):
            schedule_manager.update_last_run_status(task["schedule_id"], "Failed")


# ══════════════════════════════════════════════════════════════════════════════
# API Routes
# ══════════════════════════════════════════════════════════════════════════════

# ── Crawl ─────────────────────────────────────────────────────────────────────

@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest, background_tasks: BackgroundTasks):
    # Build request_config snapshot for re-run
    request_config = {
        "max_pages": req.max_pages,
        "max_depth": req.max_depth,
        "max_time": req.max_time,
        "delay": req.delay,
        "concurrent": req.concurrent,
        "no_robots": req.no_robots,
    }
    target_domain = extract_base_domain(normalize_url(req.url))
    task_id = task_manager.create_task(
        req.url, request_config=request_config, target_domain=target_domain
    )
    cancellation_registry.register(task_id)
    background_tasks.add_task(_run_crawl_worker, task_id, req)
    return {"task_id": task_id, "message": "Crawling started"}


# ── Tasks ─────────────────────────────────────────────────────────────────────

@app.get("/api/tasks")
def get_all_tasks():
    return task_manager.get_all_tasks()


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/status/{task_id}")
def get_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        return {"error": "Task not found"}
    return task


@app.post("/api/tasks/{task_id}/cancel", status_code=202)
def cancel_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") in {"Completed", "Failed", "Cancelled"}:
        raise HTTPException(status_code=409, detail="Task is no longer active")
    task_manager.update_task(task_id, status="CancellationRequested")
    if not cancellation_registry.request(task_id):
        cancellation_registry.register(task_id).set()
    return {"task_id": task_id, "status": "CancellationRequested"}


@app.post("/api/tasks/{task_id}/rerun")
async def rerun_task(task_id: str, background_tasks: BackgroundTasks):
    cfg = task_manager.rerun_task(task_id)
    original = task_manager.get_task(task_id)
    if not original:
        raise HTTPException(status_code=404, detail="Task not found")

    url = original["url"]
    cfg = cfg or {}
    req = CrawlRequest(
        url=url,
        max_pages=cfg.get("max_pages", 200),
        max_depth=cfg.get("max_depth", 5),
        max_time=cfg.get("max_time", 30),
        delay=cfg.get("delay", 1.5),
        concurrent=cfg.get("concurrent", 3),
        no_robots=cfg.get("no_robots", False),
    )
    target_domain = extract_base_domain(normalize_url(url))
    new_task_id = task_manager.create_task(
        url, request_config=cfg, target_domain=target_domain
    )
    cancellation_registry.register(new_task_id)
    background_tasks.add_task(_run_crawl_worker, new_task_id, req)
    return {"task_id": new_task_id, "message": "Re-run started"}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") in {"Running", "CancellationRequested"}:
        raise HTTPException(status_code=409, detail="Cannot delete an active task")
    deleted = task_manager.delete_task(task_id)
    return {"success": deleted}


# ── Reports ───────────────────────────────────────────────────────────────────

@app.get("/api/reports")
def list_reports():
    return report_manager.get_all_summaries()


@app.get("/api/reports/{domain}")
def get_report(domain: str):
    detail = report_manager.get_report_detail(domain)
    if detail is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return detail


@app.get("/api/reports/{domain}/pages")
def get_report_pages(
    domain: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    query: str = Query(default=""),
):
    result = report_manager.get_pages(domain, offset=offset, limit=limit, query=query)
    if result is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return result


@app.get("/api/reports/{domain}/download")
def download_report(domain: str, format: str = Query(default="json")):
    report_dir_candidate = report_manager.get_report_dir(domain)
    if report_dir_candidate is None:
        raise HTTPException(status_code=404, detail="Report not found")

    if format not in {"markdown", "json", "csv"}:
        raise HTTPException(status_code=422, detail="Unsupported report format")

    if format == "markdown":
        md_file = report_dir_candidate / "report.md"
        if not md_file.exists():
            raise HTTPException(status_code=404, detail="Markdown report not found")
        return StreamingResponse(
            open(md_file, "rb"),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{domain}_report.md"'},
        )
    elif format == "csv":
        generator = report_manager.stream_csv(domain)
        if generator is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return StreamingResponse(
            generator,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{domain}_pages.csv"'},
        )
    else:  # json (default)
        json_file = report_dir_candidate / "data.json"
        if not json_file.exists():
            raise HTTPException(status_code=404, detail="JSON report not found")
        return StreamingResponse(
            open(json_file, "rb"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{domain}_data.json"'},
        )


@app.delete("/api/reports/{domain}")
def delete_report(domain: str):
    active_domains = task_manager.get_active_domains()
    if domain in active_domains:
        raise HTTPException(
            status_code=409, detail="Cannot delete report while crawl is active"
        )
    deleted = report_manager.delete_report(domain, active_domains)
    if not deleted:
        raise HTTPException(status_code=404, detail="Report not found or invalid domain")
    return {"success": True}


# ── Schedules ─────────────────────────────────────────────────────────────────

@app.get("/api/schedules")
def list_schedules():
    return schedule_manager.get_all()


@app.post("/api/schedules")
def create_schedule(req: ScheduleCreateRequest):
    if not req.url:
        raise HTTPException(status_code=422, detail="URL is required")
    return schedule_manager.create(req.model_dump())


@app.put("/api/schedules/{sched_id}")
def update_schedule(sched_id: str, req: ScheduleCreateRequest):
    result = schedule_manager.update(sched_id, req.model_dump(exclude_unset=True))
    if result is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return result


@app.delete("/api/schedules/{sched_id}")
def delete_schedule(sched_id: str, cancel_active: bool = Query(default=False)):
    active_tasks = task_manager.get_active_tasks_for_schedule(sched_id)
    if active_tasks and not cancel_active:
        raise HTTPException(
            status_code=409,
            detail="Schedule has an active crawl; use cancel_active=true to request cancellation",
        )
    for task in active_tasks:
        task_manager.update_task(task["id"], status="CancellationRequested")
        cancellation_registry.request(task["id"])
    deleted = schedule_manager.delete(sched_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"success": True}


@app.post("/api/schedules/{sched_id}/toggle")
def toggle_schedule(sched_id: str):
    result = schedule_manager.toggle(sched_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return result


@app.post("/api/schedules/{sched_id}/run-now")
async def run_schedule_now(sched_id: str, background_tasks: BackgroundTasks):
    schedules = {s["id"]: s for s in schedule_manager.get_all()}
    sched = schedules.get(sched_id)
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    url = sched.get("url", "")
    cfg = sched.get("config", {})
    req = CrawlRequest(
        url=url,
        max_pages=cfg.get("max_pages", 200),
        max_depth=cfg.get("max_depth", 5),
        max_time=cfg.get("max_time", 30),
        delay=cfg.get("delay", 1.5),
        concurrent=cfg.get("concurrent", 3),
        no_robots=cfg.get("no_robots", False),
    )
    target_domain = extract_base_domain(normalize_url(url))
    task_id = task_manager.create_task(
        url,
        request_config=cfg,
        schedule_id=sched_id,
        target_domain=target_domain,
    )
    if schedule_manager.claim_schedule(sched_id, task_id) is None:
        task_manager.delete_task(task_id)
        raise HTTPException(status_code=409, detail="Schedule could not be claimed")
    cancellation_registry.register(task_id)
    background_tasks.add_task(_run_crawl_worker, task_id, req)
    return {"task_id": task_id, "message": "Schedule triggered immediately"}


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings():
    return settings_manager.get_settings()


@app.put("/api/settings")
def update_settings(req: SettingsUpdateRequest):
    updates = req.model_dump(exclude_none=True)
    return settings_manager.update_settings(updates)


@app.post("/api/settings/reset")
def reset_settings():
    return settings_manager.reset_settings()


# ── System ────────────────────────────────────────────────────────────────────

@app.get("/api/system/storage")
def get_storage():
    def _dir_size_mb(p: Path) -> float:
        if not p.exists():
            return 0.0
        total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return round(total / (1024 * 1024), 2)

    reports_count = sum(
        1 for d in OUTPUT_DIR.iterdir() if d.is_dir()
    ) if OUTPUT_DIR.exists() else 0

    return {
        "reports_count": reports_count,
        "output_size_mb": _dir_size_mb(OUTPUT_DIR),
        "data_size_mb": _dir_size_mb(DATA_DIR),
        "checkpoint_exists": CHECKPOINT_FILE.exists(),
        "checkpoint_path": str(CHECKPOINT_FILE),
    }


@app.post("/api/system/clear-checkpoint")
def clear_checkpoint():
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        return {"success": True, "message": "Checkpoint cleared"}
    return {"success": False, "message": "No checkpoint file found"}


# ── Page routes ───────────────────────────────────────────────────────────────

def _serve_html(filename: str) -> str:
    with open(FRONTEND_DIR / filename, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
def read_root():
    return _serve_html("index.html")


@app.get("/crawls", response_class=HTMLResponse)
def read_crawls():
    return _serve_html("crawls.html")


@app.get("/reports", response_class=HTMLResponse)
def read_reports():
    return _serve_html("reports.html")


@app.get("/schedules", response_class=HTMLResponse)
def read_schedules():
    return _serve_html("schedules.html")


@app.get("/settings", response_class=HTMLResponse)
def read_settings():
    return _serve_html("settings.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.api.server:app", host="127.0.0.1", port=8000, reload=True)
