"""
Universal content extractor — extract structured text, media, and links from any HTML page.

This module does NOT classify page types or extract domain-specific data.
It extracts the universal content every page has: headings, paragraphs,
lists, tables, images, videos, and links.
"""

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from models import PageData
from discovery.link_extractor import extract_all_links

logger = logging.getLogger(__name__)

# Tags whose text we strip entirely (nav/footer kept — they have useful links)
_STRIP_TAGS = {"script", "style", "noscript", "svg", "path"}


class ContentExtractor:
    """
    Extracts universal page content from raw HTML into a PageData object.

    Usage:
        extractor = ContentExtractor()
        page = extractor.extract(html, url="https://example.com/about", depth=1)
    """

    def extract(
        self,
        html: str,
        url: str = "",
        depth: int = 0,
        fetch_stage: str = "",
    ) -> PageData:
        """Extract all content from an HTML page."""
        page = PageData(url=url, depth=depth, fetch_stage=fetch_stage)

        if not html:
            return page

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Page title
        title_tag = soup.find("title")
        if title_tag:
            page.page_title = title_tag.get_text(strip=True)

        # Build a "content-only" soup for text extraction
        content_soup = self._get_content_soup(soup)

        # Headings
        page.headings = self._extract_headings(content_soup)

        # Paragraphs
        page.paragraphs = self._extract_paragraphs(content_soup)

        # Lists
        page.lists = self._extract_lists(content_soup)

        # Tables
        page.tables = self._extract_tables(content_soup)

        # Images
        page.images = self._extract_images(soup, url)

        # Videos
        page.videos = self._extract_videos(soup)

        # Links (internal / external)
        page.internal_links, page.external_links = extract_all_links(html, url)

        # Raw text + word count
        page.raw_text = self._extract_raw_text(content_soup)
        page.word_count = len(page.raw_text.split())

        logger.debug(
            f"[extract] {url} → {page.word_count} words, "
            f"{len(page.headings.get('h1', []))} h1, "
            f"{len(page.images)} images, "
            f"{len(page.internal_links)} int links"
        )

        return page

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _get_content_soup(soup: BeautifulSoup) -> BeautifulSoup:
        """
        Return a copy with script/style/noscript/svg removed.
        Keeps nav and footer (they contain useful text/links).
        """
        clone = BeautifulSoup(str(soup), "lxml")
        for tag in clone.find_all(_STRIP_TAGS):
            tag.decompose()
        return clone

    @staticmethod
    def _extract_headings(soup: BeautifulSoup) -> dict[str, list[str]]:
        """Extract h1–h6 headings."""
        headings: dict[str, list[str]] = {}
        for level in range(1, 7):
            tag_name = f"h{level}"
            found = [
                h.get_text(strip=True)
                for h in soup.find_all(tag_name)
                if h.get_text(strip=True)
            ]
            if found:
                headings[tag_name] = found
        return headings

    @staticmethod
    def _extract_paragraphs(soup: BeautifulSoup) -> list[str]:
        """Extract non-empty paragraph text."""
        paragraphs = []
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if text and len(text) > 20:  # skip tiny fragments
                paragraphs.append(text)
        return paragraphs

    @staticmethod
    def _extract_lists(soup: BeautifulSoup) -> list[list[str]]:
        """Extract ordered and unordered lists."""
        all_lists = []
        for ul_ol in soup.find_all(["ul", "ol"]):
            # Skip if this is a nested list (will be captured by parent)
            if ul_ol.parent and ul_ol.parent.name in ("li",):
                continue

            items = []
            for li in ul_ol.find_all("li", recursive=False):
                text = li.get_text(strip=True)
                if text:
                    items.append(text)

            if items and len(items) >= 2:  # skip single-item lists
                all_lists.append(items)

        return all_lists

    @staticmethod
    def _extract_tables(soup: BeautifulSoup) -> list[dict]:
        """Extract tables as {headers: [...], rows: [[...]]}."""
        tables = []

        for table in soup.find_all("table"):
            headers = []
            rows = []

            # Headers from <thead> or first <tr> with <th>
            thead = table.find("thead")
            if thead:
                for th in thead.find_all("th"):
                    headers.append(th.get_text(strip=True))
            else:
                first_tr = table.find("tr")
                if first_tr:
                    ths = first_tr.find_all("th")
                    if ths:
                        headers = [th.get_text(strip=True) for th in ths]

            # Data rows
            tbody = table.find("tbody") or table
            for tr in tbody.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                row = [cell.get_text(strip=True) for cell in cells]
                if row and any(row):  # skip empty rows
                    rows.append(row)

            if headers or rows:
                tables.append({"headers": headers, "rows": rows})

        return tables

    @staticmethod
    def _extract_images(soup: BeautifulSoup, page_url: str) -> list[dict]:
        """Extract image metadata."""
        images = []
        seen_srcs: set[str] = set()

        for img in soup.find_all("img"):
            src = img.get("src", "").strip()
            if not src:
                src = img.get("data-src", "").strip()  # lazy-loaded
            if not src:
                continue

            # Resolve relative URLs
            absolute_src = urljoin(page_url, src) if page_url else src

            # Deduplicate
            if absolute_src in seen_srcs:
                continue
            seen_srcs.add(absolute_src)

            # Skip tiny tracking pixels / icons
            width = img.get("width", "")
            height = img.get("height", "")
            if width and height:
                try:
                    if int(width) <= 2 or int(height) <= 2:
                        continue
                except (ValueError, TypeError):
                    pass

            images.append({
                "src": absolute_src,
                "alt": img.get("alt", "").strip(),
                "title": img.get("title", "").strip(),
            })

        return images

    @staticmethod
    def _extract_videos(soup: BeautifulSoup) -> list[str]:
        """Extract video embed URLs (YouTube, Vimeo, <video> tags)."""
        videos: list[str] = []
        seen: set[str] = set()

        # <iframe> embeds (YouTube, Vimeo, etc.)
        for iframe in soup.find_all("iframe", src=True):
            src = iframe["src"]
            if any(vp in src for vp in ("youtube", "vimeo", "dailymotion", "wistia")):
                if src not in seen:
                    seen.add(src)
                    videos.append(src)

        # <video> tags
        for video in soup.find_all("video"):
            src = video.get("src", "")
            if src and src not in seen:
                seen.add(src)
                videos.append(src)
            for source in video.find_all("source"):
                src = source.get("src", "")
                if src and src not in seen:
                    seen.add(src)
                    videos.append(src)

        return videos

    @staticmethod
    def _extract_raw_text(soup: BeautifulSoup) -> str:
        """Extract clean visible text from the page."""
        text = soup.get_text(separator="\n", strip=True)
        # Collapse multiple blank lines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)
