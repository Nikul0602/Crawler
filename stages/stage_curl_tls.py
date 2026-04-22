"""Stage 4: curl_cffi - TLS fingerprint impersonation."""

import time
import logging

from models import CrawlResponse, CrawlAttempt, StageResult
from config import PipelineConfig

logger = logging.getLogger(__name__)

STAGE_NAME = "curl_tls"


async def fetch(url: str, config: PipelineConfig) -> CrawlResponse:
    """
    Fetch URL using curl_cffi with TLS fingerprint impersonation.
    
    Mimics a real browser's TLS handshake to bypass fingerprint-based
    bot detection (Cloudflare, Akamai, PerimeterX).
    """
    if not config.enable_curl_tls:
        return CrawlResponse(
            success=False,
            stage_name=STAGE_NAME,
            url=url,
            attempts=[CrawlAttempt(STAGE_NAME, StageResult.DISABLED)],
        )

    start = time.perf_counter()
    attempt = CrawlAttempt(stage_name=STAGE_NAME, result=StageResult.FAILED)

    try:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession(impersonate=config.curl_impersonate) as session:
            response = await session.get(
                url,
                headers=config.default_headers,
                timeout=config.timeout,
                allow_redirects=True,
            )

            html = response.text or ""

            if response.status_code == 200 and len(html) >= config.min_content_length:
                # Quick check: is this actual content or a challenge page?
                if not _is_challenge_page(html):
                    elapsed = time.perf_counter() - start
                    attempt.result = StageResult.SUCCESS
                    attempt.content_length = len(html)
                    attempt.status_code = response.status_code
                    attempt.duration_seconds = elapsed

                    # Extract visible text from HTML
                    text = _extract_text(html)

                    logger.info(
                        f"[{STAGE_NAME}] ✅ Success ({config.curl_impersonate}): "
                        f"{len(html)} chars in {elapsed:.1f}s"
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
                else:
                    attempt.error_message = "Challenge/CAPTCHA page detected"
            else:
                attempt.error_message = f"Status {response.status_code}, length {len(html)}"

            attempt.status_code = response.status_code
            logger.warning(f"[{STAGE_NAME}] ⚠️ {attempt.error_message}")

    except ImportError:
        attempt.error_message = "curl_cffi not installed"
        logger.error(f"[{STAGE_NAME}] ❌ curl_cffi not installed")
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


def _is_challenge_page(html: str) -> bool:
    """Detect if the HTML is a bot challenge/CAPTCHA page."""
    challenge_indicators = [
        "cf-browser-verification",
        "challenge-platform",
        "Just a moment...",
        "Checking your browser",
        "Enable JavaScript and cookies",
        "Attention Required! | Cloudflare",
        "Access denied | ",
        "Please Wait... | Cloudflare",
        "_cf_chl_opt",
        "managed_checking_msg",
    ]
    html_lower = html.lower()
    return any(indicator.lower() in html_lower for indicator in challenge_indicators)


def _extract_text(html: str) -> str:
    """Extract visible text from HTML."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        # Remove script and style elements
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception:
        return ""