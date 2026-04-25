"""
Link extractor — find all internal links in an HTML page.

Extracts links from:
1. <a href="..."> tags (standard HTML)
2. Markdown-style [text](url) patterns (from rendered content)
3. Next.js / SPA data attributes that contain route paths

This multi-strategy approach ensures we catch links even on
JavaScript-heavy sites like Next.js, React, Angular, etc.
"""

import re
import logging
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from discovery.url_utils import normalize_url, is_same_domain, should_exclude

logger = logging.getLogger(__name__)

# Regex to find markdown-style links: [text](url)
_MARKDOWN_LINK_RE = re.compile(r'\[([^\]]*)\]\((https?://[^\s\)]+)\)')

# Regex to find bare URLs in text
_BARE_URL_RE = re.compile(r'https?://[^\s\'"<>\)\]]+')

# Next.js / React data attributes that commonly hold route paths
_SPA_HREF_ATTRS = ["href", "data-href", "data-url", "data-link", "data-route"]


def extract_internal_links(
    html: str,
    page_url: str,
    base_domain: str,
    markdown_text: str = "",
) -> list[str]:
    """
    Extract all unique internal links from an HTML page.

    Args:
        html: Raw HTML content.
        page_url: The URL of the page being parsed (for resolving relative links).
        base_domain: The base domain to filter against (e.g. "example.com").
        markdown_text: Optional markdown/text content to also mine for links.

    Returns:
        Sorted, deduplicated list of normalized internal URLs.
    """
    internal, _ = _extract_links_multi(html, page_url, base_domain, markdown_text)
    return internal


def extract_all_links(
    html: str,
    page_url: str,
    markdown_text: str = "",
) -> tuple[list[str], list[str]]:
    """
    Extract ALL links from HTML + markdown, split into internal and external.

    Args:
        html: Raw HTML content.
        page_url: The URL of the page being parsed.
        markdown_text: Optional markdown/text content to also mine for links.

    Returns:
        Tuple of (internal_links, external_links), both sorted and deduplicated.
    """
    from discovery.url_utils import extract_base_domain
    base_domain = extract_base_domain(page_url)
    return _extract_links_multi(html, page_url, base_domain, markdown_text)


def _extract_links_multi(
    html: str,
    page_url: str,
    base_domain: str,
    markdown_text: str = "",
) -> tuple[list[str], list[str]]:
    """
    Core link extraction using multiple strategies.

    Strategy 1: <a href> tags from HTML
    Strategy 2: Any element with href/data-href attributes (SPA routes)
    Strategy 3: Markdown [text](url) patterns from markdown content
    Strategy 4: Bare URLs in markdown/text as last resort
    """
    internal: set[str] = set()
    external: set[str] = set()

    # ── Strategy 1+2: Parse HTML for links ──
    if html:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Strategy 1: Standard <a href="..."> links
        for anchor in soup.find_all("a", href=True):
            _classify_href(anchor["href"], page_url, base_domain, internal, external)

        # Strategy 2: Elements with SPA data attributes (data-href, data-url, etc.)
        for attr in _SPA_HREF_ATTRS[1:]:  # skip "href" — already handled above
            for el in soup.find_all(attrs={attr: True}):
                _classify_href(el[attr], page_url, base_domain, internal, external)

        # Strategy 2b: Next.js prefetch links (<link rel="prefetch" href="/_next/...">
        #              and any <link> with an internal href in the body)
        for link_tag in soup.find_all("link", href=True):
            href = link_tag["href"]
            # Skip stylesheets, icons, dns-prefetch etc.
            rel = link_tag.get("rel", [])
            if isinstance(rel, list):
                rel = " ".join(rel)
            if any(skip in str(rel).lower() for skip in ("stylesheet", "icon", "dns-", "preconnect")):
                continue
            _classify_href(href, page_url, base_domain, internal, external)

    # ── Strategy 3: Markdown [text](url) links ──
    text_to_scan = markdown_text or ""
    if text_to_scan:
        for match in _MARKDOWN_LINK_RE.finditer(text_to_scan):
            url = match.group(2).strip()
            _classify_href(url, page_url, base_domain, internal, external)

    # ── Strategy 4: Bare URLs in text (if we found very few links) ──
    if len(internal) < 3 and text_to_scan:
        for match in _BARE_URL_RE.finditer(text_to_scan):
            url = match.group(0).rstrip(".,;:!?)")
            _classify_href(url, page_url, base_domain, internal, external)

    result_int = sorted(internal)
    result_ext = sorted(external)

    logger.debug(
        f"[links] {page_url} -> {len(result_int)} internal, {len(result_ext)} external"
    )

    return result_int, result_ext


def _classify_href(
    raw_href: str,
    page_url: str,
    base_domain: str,
    internal: set[str],
    external: set[str],
) -> None:
    """Normalize a single href and add it to the internal or external set."""
    raw_href = (raw_href or "").strip()
    if not raw_href:
        return

    if should_exclude(raw_href):
        return

    # Resolve relative URLs
    absolute = urljoin(page_url, raw_href)
    normalized = normalize_url(absolute)

    if not normalized:
        return

    if is_same_domain(normalized, base_domain):
        internal.add(normalized)
    else:
        external.add(normalized)
