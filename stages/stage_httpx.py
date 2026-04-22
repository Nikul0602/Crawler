"""Stage 6: Plain httpx - Final fallback."""

import time
import logging

from models import CrawlResponse, CrawlAttempt, StageResult
from config import PipelineConfig

logger = logging.getLogger(__name__)

STAGE_NAME = "httpx_plain"


async def fetch(url: str, config: PipelineConfig) -> CrawlResponse:
    """
    Fetch URL using plain httpx with no impersonation.
    
    Last resort — simplest possible HTTP request.
    Works for sites with no bot protection.
    """
    if not config.enable_httpx:
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

        headers = {
            **config.default_headers,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout),
            follow_redirects=True,
            http2=True,
        ) as client:
            response = await client.get(url, headers=headers)

            html = response.text or ""
            attempt.status_code = response.status_code
            attempt.content_length = len(html)
            attempt.duration_seconds = time.perf_counter() - start

            if response.status_code == 200 and len(html) >= config.min_content_length:
                attempt.result = StageResult.SUCCESS

                text = _extract_text(html)

                logger.info(
                    f"[{STAGE_NAME}] ✅ Success: {len(html)} chars "
                    f"in {attempt.duration_seconds:.1f}s"
                )

                return CrawlResponse(
                    success=True,
                    html=html,
                    text=text,
                    status_code=response.status_code,
                    stage_name=STAGE_NAME,
                    attempts=[attempt],
                    url=url,
                )

            attempt.error_message = f"Status {response.status_code}, length {len(html)}"
            logger.warning(f"[{STAGE_NAME}] ⚠️ {attempt.error_message}")

    except ImportError:
        attempt.error_message = "httpx not installed"
        logger.error(f"[{STAGE_NAME}] ❌ httpx not installed")
    except Exception as e:
        attempt.error_message = str(e)
        attempt.duration_seconds = time.perf_counter() - start
        logger.error(f"[{STAGE_NAME}] ❌ Error: {e}")

    return CrawlResponse(
        success=False,
        stage_name=STAGE_NAME,
        url=url,
        attempts=[attempt],
    )


def _extract_text(html: str) -> str:
    """Extract visible text from HTML."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception:
        return ""