import sys
import asyncio
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Fix for Playwright/asyncio NotImplementedError on Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from backend.services.task_manager import task_manager
from backend.core.config import PipelineConfig, CrawlerConfig
from backend.crawler.orchestrator import CrawlOrchestrator
from backend.exporters.markdown_report import generate_markdown_report
from backend.exporters.json_export import export_json
from backend.paths import FRONTEND_DIR, OUTPUT_DIR

app = FastAPI(title="CrawlMaster API")

# Ensure static and output directories exist
FRONTEND_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Mount static files (HTML, CSS, JS)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

class CrawlRequest(BaseModel):
    url: str
    max_pages: int = 200
    max_depth: int = 5
    max_time: int = 30
    delay: float = 1.5
    concurrent: int = 3
    no_robots: bool = False

def background_crawl_task(task_id: str, req: CrawlRequest):
    """Sync wrapper to run the async task in a separate thread with a fresh event loop."""
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    # Run the actual async task in a new event loop
    asyncio.run(_async_crawl_task(task_id, req))


async def _async_crawl_task(task_id: str, req: CrawlRequest):
    try:
        task_manager.update_task(task_id, status="Running")
        
        pipeline_config = PipelineConfig(timeout=30)
        crawler_config = CrawlerConfig(
            max_pages=req.max_pages,
            max_discovered_urls=1000,
            max_depth=req.max_depth,
            max_time_minutes=req.max_time,
            request_delay=req.delay,
            max_concurrent=req.concurrent,
            respect_robots=not req.no_robots,
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
            # Calculate progress percentage
            progress_percent = int((completed / total) * 100) if total > 0 else 0
            task_manager.update_task(task_id, progress=progress_percent, pages_crawled=completed)

        report = await orchestrator.crawl(
            url=req.url,
            resume=False,
            progress_callback=progress_callback,
        )

        # Generate outputs
        output_dir = OUTPUT_DIR / report.domain
        output_dir.mkdir(parents=True, exist_ok=True)
        md_path = generate_markdown_report(report, output_dir)
        json_path = export_json(report, output_dir)

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
            duration=duration_str
        )

    except Exception as e:
        task_manager.update_task(task_id, status="Failed", error=str(e), completed_at=datetime.now().isoformat())


@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest, background_tasks: BackgroundTasks):
    task_id = task_manager.create_task(req.url)
    background_tasks.add_task(background_crawl_task, task_id, req)
    return {"task_id": task_id, "message": "Crawling started"}

@app.get("/api/status/{task_id}")
def get_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        return {"error": "Task not found"}
    return task

@app.get("/api/tasks")
def get_all_tasks():
    return task_manager.get_all_tasks()

@app.get("/", response_class=HTMLResponse)
def read_root():
    with open(FRONTEND_DIR / "index.html", "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.api.server:app", host="0.0.0.0", port=8000, reload=True)

