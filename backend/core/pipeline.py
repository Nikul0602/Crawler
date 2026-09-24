"""
Main pipeline orchestrator — chains all 6 stages with fallback logic.
"""

import asyncio
import logging
import time
from typing import Optional

from backend.core.models import CrawlResponse, CrawlAttempt, StageResult
from backend.core.config import PipelineConfig
from backend.stages import (
    stage_crawl4ai,
    stage_scrapling,
    stage_jina,
    stage_curl_tls,
    stage_curl_rotated,
    stage_httpx,
)

logger = logging.getLogger(__name__)


class CrawlPipeline:
    """
    6-stage fallback pipeline for resilient web crawling.
    
    Stages run sequentially. As soon as one succeeds, the pipeline
    returns its result. If all stages fail, a failure response is returned
    with details from every attempt.
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()

        # Ordered list of (name, fetcher_coroutine) tuples
        self.stages = [
            ("Crawl4AI",        stage_crawl4ai.fetch),
            ("Scrapling",       stage_scrapling.fetch),
            ("Jina Reader",     stage_jina.fetch),
            ("curl_cffi TLS",   stage_curl_tls.fetch),
            ("curl_cffi Rotated", stage_curl_rotated.fetch),
            ("httpx Plain",     stage_httpx.fetch),
        ]

    async def crawl(self, url: str) -> CrawlResponse:
        """
        Run the URL through the pipeline. Returns the first successful result.
        """
        all_attempts: list[CrawlAttempt] = []
        pipeline_start = time.perf_counter()

        logger.info(f"{'='*60}")
        logger.info(f"🚀 Pipeline START: {url}")
        logger.info(f"{'='*60}")

        for i, (name, fetcher) in enumerate(self.stages, 1):
            event = getattr(self.config, "cancellation_event", None)
            if event is not None and event.is_set():
                return CrawlResponse(
                    success=False,
                    stage_name="cancelled",
                    url=url,
                    attempts=all_attempts,
                )
            logger.info(f"\n--- Stage {i}/6: {name} ---")

            try:
                result = await fetcher(url, self.config)
            except Exception as e:
                # Catch-all: a stage should never crash the pipeline
                logger.error(f"[{name}] 💥 Unhandled exception: {e}")
                all_attempts.append(CrawlAttempt(
                    stage_name=name,
                    result=StageResult.FAILED,
                    error_message=f"Unhandled: {e}",
                ))
                continue

            # Collect attempts for the report
            all_attempts.extend(result.attempts)

            if result.success:
                total_time = time.perf_counter() - pipeline_start
                logger.info(f"\n{'='*60}")
                logger.info(
                    f"✅ Pipeline SUCCESS via '{name}' "
                    f"({len(result.content)} chars, {total_time:.1f}s total)"
                )
                logger.info(f"{'='*60}")

                # Attach all attempts (including failed ones) for diagnostics
                result.attempts = all_attempts
                return result

            logger.info(f"[{name}] ❌ Failed — falling through to next stage")

        # All stages failed
        total_time = time.perf_counter() - pipeline_start
        logger.error(f"\n{'='*60}")
        logger.error(f"❌ Pipeline FAILED: All 6 stages exhausted ({total_time:.1f}s)")
        logger.error(f"{'='*60}")

        return CrawlResponse(
            success=False,
            stage_name="pipeline_exhausted",
            url=url,
            attempts=all_attempts,
        )

    def print_report(self, response: CrawlResponse) -> None:
        """Print a human-readable report of the pipeline execution."""
        print(f"\n{'='*60}")
        print(f"📊 PIPELINE REPORT for: {response.url}")
        print(f"{'='*60}")
        print(f"Result: {'✅ SUCCESS' if response.success else '❌ FAILED'}")
        if response.success:
            print(f"Winning stage: {response.stage_name}")
            print(f"Content length: {len(response.content)} chars")
        print(f"\nAttempt Details:")
        print(f"{'-'*60}")

        for attempt in response.attempts:
            icon = {
                StageResult.SUCCESS: "✅",
                StageResult.FAILED: "❌",
                StageResult.DISABLED: "⏭️",
                StageResult.SKIPPED: "⏩",
            }.get(attempt.result, "❓")

            print(
                f"  {icon} {attempt.stage_name:<25} "
                f"| {attempt.result.value:<8} "
                f"| {attempt.duration_seconds:.1f}s "
                f"| {attempt.content_length} chars"
            )
            if attempt.error_message:
                print(f"     └─ {attempt.error_message}")

        print(f"{'='*60}\n")

