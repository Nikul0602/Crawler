"""
Link extractor — find all internal links in an HTML page.

Parses <a href="..."> tags, normalizes URLs, and filters to
same-domain internal links only.
"""

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from discovery.url_utils import normalize_url, is_same_domain, should_exclude

logger = logging.getLogger(__name__)


def extract_internal_links(
    html: str,
    page_url: str,
    base_domain: str,
) -> list[str]:
    """
    Extract all unique internal links from an HTML page.

    Args:
        html: Raw HTML content.
        page_url: The URL of the page being parsed (for resolving relative links).
        base_domain: The base domain to filter against (e.g. "example.com").

    Returns:
        Sorted, deduplicated list of normalized internal URLs.
    """
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    found: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        raw_href = anchor.get("href", "").strip()

        if not raw_href:
            continue

        # Skip excluded URLs (mailto, tel, javascript, binary files, etc.)
        if should_exclude(raw_href):
            continue

        # Resolve relative URLs
        absolute = urljoin(page_url, raw_href)

        # Normalize
        normalized = normalize_url(absolute)
        if not normalized:
            continue

        # Keep only same-domain links
        if is_same_domain(normalized, base_domain):
            found.add(normalized)

    result = sorted(found)
    logger.debug(
        f"[links] Extracted {len(result)} internal links from {page_url}"
    )
    return result


def extract_all_links(
    html: str,
    page_url: str,
) -> tuple[list[str], list[str]]:
    """
    Extract ALL links from HTML, split into internal and external.

    Args:
        html: Raw HTML content.
        page_url: The URL of the page being parsed.

    Returns:
        Tuple of (internal_links, external_links), both sorted and deduplicated.
    """
    if not html:
        return [], []

    from discovery.url_utils import extract_base_domain

    base_domain = extract_base_domain(page_url)

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    internal: set[str] = set()
    external: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        raw_href = anchor.get("href", "").strip()

        if not raw_href or should_exclude(raw_href):
            continue

        absolute = urljoin(page_url, raw_href)
        normalized = normalize_url(absolute)

        if not normalized:
            continue

        if is_same_domain(normalized, base_domain):
            internal.add(normalized)
        else:
            external.add(normalized)

    return sorted(internal), sorted(external)
