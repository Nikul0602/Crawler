"""Stage 1: Crawl4AI - Primary browser-based extraction."""

import asyncio
import time
import logging
from typing import Optional

from backend.core.models import CrawlResponse, CrawlAttempt, StageResult
from backend.core.config import PipelineConfig

logger = logging.getLogger(__name__)

STAGE_NAME = "crawl4ai"


async def fetch(url: str, config: PipelineConfig) -> CrawlResponse:
    """
    Fetch URL using Crawl4AI with full browser rendering.
    
    Crawl4AI launches a headless Chromium, navigates to the URL,
    waits for JS to render, and extracts content as Markdown + HTML.
    """
    if not config.enable_crawl4ai:
        return CrawlResponse(
            success=False,
            stage_name=STAGE_NAME,
            url=url,
            attempts=[CrawlAttempt(STAGE_NAME, StageResult.DISABLED)],
        )

    start = time.perf_counter()
    attempt = CrawlAttempt(stage_name=STAGE_NAME, result=StageResult.FAILED)

    try:
        # Import here to avoid import errors if not installed
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        browser_cfg = BrowserConfig(
            headless=config.crawl4ai_headless,
            verbose=False,
        )

        run_cfg = CrawlerRunConfig(
            wait_until="domcontentloaded",       # Use domcontentloaded to avoid timeout on anti-bot sites that keep network active indefinitely
            page_timeout=config.timeout * 1000,  # milliseconds
            wait_for=config.crawl4ai_wait_for or None,
            js_code=config.crawl4ai_js_code or None,
            delay_before_return_html=config.crawl4ai_wait_seconds,
        )

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)

            if result.success and result.markdown and len(result.markdown.raw_markdown) >= config.min_content_length:
                elapsed = time.perf_counter() - start
                attempt.result = StageResult.SUCCESS
                attempt.content_length = len(result.markdown.raw_markdown)
                attempt.status_code = result.status_code
                attempt.duration_seconds = elapsed

                logger.info(
                    f"[{STAGE_NAME}] ✅ Success: {len(result.markdown.raw_markdown)} chars "
                    f"in {elapsed:.1f}s"
                )

                return CrawlResponse(
                    success=True,
                    html=result.html or "",
                    markdown=result.markdown.raw_markdown,
                    text=result.markdown.raw_markdown,
                    status_code=result.status_code,
                    stage_name=STAGE_NAME,
                    attempts=[attempt],
                    url=url,
                )
            else:
                attempt.error_message = "Empty or insufficient content returned"
                attempt.status_code = getattr(result, 'status_code', 0)
                logger.warning(f"[{STAGE_NAME}] ⚠️ Insufficient content")

    except ImportError:
        attempt.error_message = "crawl4ai not installed"
        logger.error(f"[{STAGE_NAME}] ❌ crawl4ai not installed")
    except Exception as e:
        attempt.error_message = str(e)
        logger.error(f"[{STAGE_NAME}] ❌ Error: {e}")

    attempt.duration_seconds = time.perf_counter() - start
    return CrawlResponse(
        success=False,
        stage_name=STAGE_NAME,
        url=url,
        attempts=[attempt],
    )
