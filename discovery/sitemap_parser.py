"""
Sitemap parser — discover all URLs from sitemap.xml variants.

Handles:
- Standard sitemap.xml
- Sitemap index files (sitemap_index.xml)
- WordPress-style sitemaps (/wp-sitemap.xml, /page-sitemap.xml, etc.)
- Sitemap URLs declared in robots.txt
- Gzipped sitemaps
"""

import gzip
import logging
from io import BytesIO
from urllib.parse import urlparse, urljoin

import httpx
from lxml import etree

logger = logging.getLogger(__name__)

# Common sitemap paths to try (in priority order)
SITEMAP_PATHS = [
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/wp-sitemap.xml",
    "/sitemap/sitemap-index.xml",
    "/page-sitemap.xml",
    "/post-sitemap.xml",
]

# XML namespaces used in sitemaps
_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
}


class SitemapParser:
    """
    Discovers and parses all sitemap files for a domain.

    Usage:
        parser = SitemapParser("https://example.com", timeout=15)
        urls = await parser.discover()
        # urls → ["https://example.com/about", "https://example.com/blog", ...]
    """

    def __init__(self, base_url: str, timeout: int = 15):
        parsed = urlparse(base_url)
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        self._timeout = timeout
        self._discovered_urls: set[str] = set()
        self._processed_sitemaps: set[str] = set()

    async def discover(
        self, extra_sitemap_urls: list[str] | None = None,
    ) -> list[str]:
        """
        Discover all page URLs from sitemaps.

        Args:
            extra_sitemap_urls: Additional sitemap URLs (e.g. from robots.txt).

        Returns:
            Deduplicated list of discovered page URLs.
        """
        # Collect candidate sitemap URLs
        candidates: list[str] = []

        # 1. Standard paths
        for path in SITEMAP_PATHS:
            candidates.append(f"{self._origin}{path}")

        # 2. Extra URLs from robots.txt
        if extra_sitemap_urls:
            candidates.extend(extra_sitemap_urls)

        # Try each candidate
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=True,
        ) as client:
            for sitemap_url in candidates:
                if sitemap_url in self._processed_sitemaps:
                    continue
                await self._fetch_and_parse(client, sitemap_url)

        urls = sorted(self._discovered_urls)
        logger.info(f"[sitemap] 📍 Discovered {len(urls)} URLs from sitemaps")
        return urls

    async def _fetch_and_parse(
        self, client: httpx.AsyncClient, url: str,
    ) -> None:
        """Fetch a single sitemap URL and parse its contents."""
        if url in self._processed_sitemaps:
            return
        self._processed_sitemaps.add(url)

        try:
            response = await client.get(url)
            if response.status_code != 200:
                return

            content = response.content

            # Handle gzipped sitemaps
            if url.endswith(".gz") or response.headers.get(
                "content-type", ""
            ).startswith("application/x-gzip"):
                try:
                    content = gzip.decompress(content)
                except Exception:
                    pass  # not actually gzipped, try raw

            # Parse XML
            try:
                root = etree.fromstring(content)
            except etree.XMLSyntaxError:
                logger.debug(f"[sitemap] ⚠️  Invalid XML: {url}")
                return

            tag = etree.QName(root.tag).localname if root.tag else ""

            if tag == "sitemapindex":
                # Sitemap index → recurse into child sitemaps
                for sitemap_el in root.findall("sm:sitemap/sm:loc", _NS):
                    child_url = (sitemap_el.text or "").strip()
                    if child_url:
                        await self._fetch_and_parse(client, child_url)

            elif tag == "urlset":
                # Regular sitemap → extract page URLs
                for url_el in root.findall("sm:url/sm:loc", _NS):
                    page_url = (url_el.text or "").strip()
                    if page_url:
                        self._discovered_urls.add(page_url)

            logger.debug(
                f"[sitemap] ✅ Parsed {url} — "
                f"{len(self._discovered_urls)} URLs so far"
            )

        except httpx.TimeoutException:
            logger.debug(f"[sitemap] ⏱️  Timeout fetching {url}")
        except Exception as e:
            logger.debug(f"[sitemap] ⚠️  Error fetching {url}: {e}")
