"""
Robots.txt parser — fetch and check crawl permissions.

Uses Python's built-in urllib.robotparser with an async httpx fetch.
"""

import logging
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

# Default user-agent we identify as
DEFAULT_USER_AGENT = "UniversalCrawler/1.0"


class RobotsChecker:
    """
    Fetches and parses robots.txt for a given domain.

    Usage:
        checker = await RobotsChecker.from_url("https://example.com")
        if checker.is_allowed("https://example.com/secret"):
            ...
    """

    def __init__(self):
        self._parser = RobotFileParser()
        self._loaded = False
        self._sitemaps: list[str] = []
        self.user_agent = DEFAULT_USER_AGENT

    @classmethod
    async def from_url(
        cls,
        base_url: str,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 10,
    ) -> "RobotsChecker":
        """Fetch robots.txt and return a ready-to-use checker."""
        instance = cls()
        instance.user_agent = user_agent
        await instance._fetch(base_url, timeout)
        return instance

    async def _fetch(self, base_url: str, timeout: int) -> None:
        """Download and parse robots.txt."""
        parsed = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                follow_redirects=True,
            ) as client:
                response = await client.get(robots_url)

            if response.status_code == 200:
                content = response.text
                self._parser.parse(content.splitlines())
                self._loaded = True

                # Extract Sitemap: directives
                for line in content.splitlines():
                    line = line.strip()
                    if line.lower().startswith("sitemap:"):
                        sitemap_url = line.split(":", 1)[1].strip()
                        if sitemap_url:
                            self._sitemaps.append(sitemap_url)

                logger.info(
                    f"[robots] ✅ Parsed robots.txt — "
                    f"{len(self._sitemaps)} sitemap(s) found"
                )
            else:
                # No robots.txt → everything is allowed
                self._loaded = False
                logger.info(
                    f"[robots] ℹ️  robots.txt returned {response.status_code} — "
                    f"assuming all paths allowed"
                )

        except Exception as e:
            self._loaded = False
            logger.warning(f"[robots] ⚠️  Could not fetch robots.txt: {e}")

    def is_allowed(self, url: str) -> bool:
        """Check if the given URL is allowed by robots.txt rules."""
        if not self._loaded:
            return True  # no robots.txt → allow everything

        return self._parser.can_fetch(self.user_agent, url)

    @property
    def sitemaps(self) -> list[str]:
        """Return sitemap URLs declared in robots.txt."""
        return list(self._sitemaps)

    @property
    def crawl_delay(self) -> float | None:
        """Return Crawl-delay value if specified, else None."""
        if not self._loaded:
            return None
        try:
            delay = self._parser.crawl_delay(self.user_agent)
            return float(delay) if delay is not None else None
        except Exception:
            return None
