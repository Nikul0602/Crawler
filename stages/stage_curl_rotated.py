"""Stage 5: curl_cffi with rotated browser profiles and jitter."""

import asyncio
import random
import time
import logging

from models import CrawlResponse, CrawlAttempt, StageResult
from config import PipelineConfig

logger = logging.getLogger(__name__)

STAGE_NAME = "curl_rotated"


async def fetch(url: str, config: PipelineConfig) -> CrawlResponse:
    """
    Fetch URL using curl_cffi, rotating through different browser
    impersonation profiles with exponential backoff and jitter.
    
    Each retry uses:
    - A different browser TLS fingerprint (Chrome → Firefox → Safari → Edge)
    - Exponential backoff: 2s, 4s, 8s, 16s...
    - Random jitter added to each delay to avoid detection patterns
    """
    if not config.enable_curl_rotated:
        return CrawlResponse(
            success=False,
            stage_name=STAGE_NAME,
            url=url,
            attempts=[CrawlAttempt(STAGE_NAME, StageResult.DISABLED)],
        )

    attempts: list[CrawlAttempt] = []
    profiles = config.curl_rotated_profiles

    try:
        from curl_cffi.requests import AsyncSession

        for i in range(config.curl_rotated_max_retries):
            profile = profiles[i % len(profiles)]
            attempt = CrawlAttempt(
                stage_name=f"{STAGE_NAME}[{profile}]",
                result=StageResult.FAILED,
            )
            start = time.perf_counter()

            try:
                async with AsyncSession(impersonate=profile) as session:
                    response = await session.get(
                        url,
                        headers=config.default_headers,
                        timeout=config.timeout,
                        allow_redirects=True,
                    )

                    html = response.text or ""
                    attempt.status_code = response.status_code
                    attempt.content_length = len(html)
                    attempt.duration_seconds = time.perf_counter() - start

                    if (
                        response.status_code == 200
                        and len(html) >= config.min_content_length
                        and not _is_challenge_page(html)
                    ):
                        attempt.result = StageResult.SUCCESS
                        attempts.append(attempt)

                        text = _extract_text(html)

                        logger.info(
                            f"[{STAGE_NAME}] ✅ Success on attempt {i+1}/{config.curl_rotated_max_retries} "
                            f"(profile: {profile}): {len(html)} chars"
                        )

                        return CrawlResponse(
                            success=True,
                            html=html,
                            text=text,
                            status_code=response.status_code,
                            stage_name=STAGE_NAME,
                            attempts=attempts,
                            url=url,
                        )

                    attempt.error_message = (
                        f"Status {response.status_code}, "
                        f"length {len(html)}, "
                        f"challenge={'yes' if _is_challenge_page(html) else 'no'}"
                    )

            except Exception as e:
                attempt.error_message = str(e)
                attempt.duration_seconds = time.perf_counter() - start

            attempts.append(attempt)
            logger.warning(
                f"[{STAGE_NAME}] ⚠️ Attempt {i+1}/{config.curl_rotated_max_retries} "
                f"({profile}): {attempt.error_message}"
            )

            # Wait before next retry (except on last attempt)
            if i < config.curl_rotated_max_retries - 1:
                # Exponential backoff with jitter
                base_delay = config.curl_rotated_base_delay * (2 ** i)
                jitter = random.uniform(0, config.curl_rotated_max_jitter * (i + 1))
                delay = base_delay + jitter
                logger.debug(f"[{STAGE_NAME}] 💤 Waiting {delay:.1f}s before retry...")
                await asyncio.sleep(delay)

    except ImportError:
        attempts.append(CrawlAttempt(
            stage_name=STAGE_NAME,
            result=StageResult.FAILED,
            error_message="curl_cffi not installed",
        ))
        logger.error(f"[{STAGE_NAME}] ❌ curl_cffi not installed")

    logger.error(f"[{STAGE_NAME}] ❌ All {config.curl_rotated_max_retries} attempts failed")
    return CrawlResponse(
        success=False,
        stage_name=STAGE_NAME,
        url=url,
        attempts=attempts,
    )


def _is_challenge_page(html: str) -> bool:
    """Detect if the HTML is a bot challenge/CAPTCHA page."""
    challenge_indicators = [
        "cf-browser-verification", "challenge-platform",
        "Just a moment...", "Checking your browser",
        "_cf_chl_opt", "managed_checking_msg",
    ]
    html_lower = html.lower()
    return any(ind.lower() in html_lower for ind in challenge_indicators)


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