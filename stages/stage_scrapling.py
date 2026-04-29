"""Stage 2: Scrapling - Reserve browser extraction with stealth."""

import time
import logging

from core.models import CrawlResponse, CrawlAttempt, StageResult
from core.config import PipelineConfig

logger = logging.getLogger(__name__)

STAGE_NAME = "scrapling"


async def fetch(url: str, config: PipelineConfig) -> CrawlResponse:
    """
    Fetch URL using Scrapling's stealth browser.
    
    Uses StealthyFetcher which patches browser fingerprints,
    randomizes properties, and simulates human-like behavior.
    """
    if not config.enable_scrapling:
        return CrawlResponse(
            success=False,
            stage_name=STAGE_NAME,
            url=url,
            attempts=[CrawlAttempt(STAGE_NAME, StageResult.DISABLED)],
        )

    start = time.perf_counter()
    attempt = CrawlAttempt(stage_name=STAGE_NAME, result=StageResult.FAILED)

    try:
        from scrapling import StealthyFetcher

        fetcher = StealthyFetcher(
            auto_match=True,    # Automatically match elements even if page changes
        )

        # StealthyFetcher.fetch is synchronous but runs browser internally
        # We run it in a thread to keep our async pipeline flowing
        import asyncio
        response = await asyncio.to_thread(
            fetcher.fetch,
            url,
            headless=config.scrapling_headless,
            network_idle=True,
            timeout=config.timeout * 1000,
        )

        if response and response.status == 200:
            # Get the full page text content
            page_text = response.get_all_text() if hasattr(response, 'get_all_text') else ""
            page_html = response.html_content if hasattr(response, 'html_content') else str(response)

            # Fallback: convert adaptor to text
            if not page_text and hasattr(response, 'text'):
                page_text = response.text

            content = page_text or page_html or ""

            if len(content) >= config.min_content_length:
                elapsed = time.perf_counter() - start
                attempt.result = StageResult.SUCCESS
                attempt.content_length = len(content)
                attempt.status_code = response.status
                attempt.duration_seconds = elapsed

                logger.info(
                    f"[{STAGE_NAME}] ✅ Success: {len(content)} chars in {elapsed:.1f}s"
                )

                return CrawlResponse(
                    success=True,
                    html=page_html,
                    text=page_text,
                    status_code=response.status,
                    stage_name=STAGE_NAME,
                    attempts=[attempt],
                    url=url,
                )

        attempt.error_message = f"Status: {getattr(response, 'status', 'unknown')}"
        attempt.status_code = getattr(response, 'status', 0)
        logger.warning(f"[{STAGE_NAME}] ⚠️ {attempt.error_message}")

    except ImportError:
        attempt.error_message = "scrapling not installed"
        logger.error(f"[{STAGE_NAME}] ❌ scrapling not installed")
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