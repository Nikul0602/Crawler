"""Stage 3: Jina Reader Proxy - Cloud-based rendering and extraction."""

import time
import logging

from models import CrawlResponse, CrawlAttempt, StageResult
from config import PipelineConfig

logger = logging.getLogger(__name__)

STAGE_NAME = "jina_reader"


async def fetch(url: str, config: PipelineConfig) -> CrawlResponse:
    """
    Fetch URL through Jina's r.jina.ai reader proxy.
    
    Sends the URL to Jina's servers which render the page
    and return clean Markdown. Uses httpx for the API call itself.
    """
    if not config.enable_jina:
        return CrawlResponse(
            success=False,
            stage_name=STAGE_NAME,
            url=url,
            attempts=[CrawlAttempt(STAGE_NAME, StageResult.DISABLED)],
        )

    start = time.perf_counter()
    attempt = CrawlAttempt(stage_name=STAGE_NAME, result=StageResult.FAILED)

    try:
        import httpx

        # Construct the Jina reader URL
        jina_url = f"{config.jina_base_url.rstrip('/')}/{url}"

        headers = {
            "Accept": "text/markdown",
            "X-With-Links": "true",
            "X-With-Images": "false",
            "X-Timeout": str(config.timeout),
        }

        # Add API key if available (higher rate limits)
        if config.jina_api_key:
            headers["Authorization"] = f"Bearer {config.jina_api_key}"

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout + 10),  # Extra buffer for Jina processing
            follow_redirects=True,
        ) as client:
            response = await client.get(jina_url, headers=headers)

            if response.status_code == 200 and len(response.text) >= config.min_content_length:
                elapsed = time.perf_counter() - start
                attempt.result = StageResult.SUCCESS
                attempt.content_length = len(response.text)
                attempt.status_code = response.status_code
                attempt.duration_seconds = elapsed

                logger.info(
                    f"[{STAGE_NAME}] ✅ Success: {len(response.text)} chars in {elapsed:.1f}s"
                )

                return CrawlResponse(
                    success=True,
                    markdown=response.text,
                    text=response.text,
                    status_code=response.status_code,
                    stage_name=STAGE_NAME,
                    attempts=[attempt],
                    url=url,
                )

            attempt.status_code = response.status_code
            attempt.error_message = f"Status {response.status_code}, length {len(response.text)}"
            logger.warning(f"[{STAGE_NAME}] ⚠️ {attempt.error_message}")

    except ImportError:
        attempt.error_message = "httpx not installed"
        logger.error(f"[{STAGE_NAME}] ❌ httpx not installed")
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