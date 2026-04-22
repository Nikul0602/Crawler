"""
Meta / SEO extractor — extract page metadata from <head>.

Extracts: title, description, keywords, Open Graph tags, canonical URL,
language, and JSON-LD structured data.
"""

import json
import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def extract_meta(html: str) -> dict:
    """
    Extract SEO and meta information from an HTML page's <head>.

    Returns:
        {
            "page_title": "...",
            "meta_description": "...",
            "meta_keywords": "...",
            "og_title": "...",
            "og_image": "...",
            "og_description": "...",
            "canonical_url": "...",
            "language": "...",
            "structured_data": [{...}, ...],
        }
    """
    result = {
        "page_title": "",
        "meta_description": "",
        "meta_keywords": "",
        "og_title": "",
        "og_image": "",
        "og_description": "",
        "canonical_url": "",
        "language": "",
        "structured_data": [],
    }

    if not html:
        return result

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # ── <title> ──
    title_tag = soup.find("title")
    if title_tag:
        result["page_title"] = title_tag.get_text(strip=True)

    # ── <meta> tags ──
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        content = meta.get("content", "").strip()

        if not content:
            continue

        if name == "description":
            result["meta_description"] = content
        elif name == "keywords":
            result["meta_keywords"] = content
        elif name == "og:title":
            result["og_title"] = content
        elif name == "og:image":
            result["og_image"] = content
        elif name == "og:description":
            result["og_description"] = content

    # ── <link rel="canonical"> ──
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        result["canonical_url"] = canonical["href"].strip()

    # ── <html lang="..."> ──
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        result["language"] = html_tag["lang"].strip()

    # ── JSON-LD structured data ──
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                result["structured_data"].append(data)
            elif isinstance(data, list):
                result["structured_data"].extend(data)
        except (json.JSONDecodeError, TypeError):
            pass

    return result
