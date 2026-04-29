"""
Markdown report generator — produces a comprehensive website report.

Uses Jinja2 for templating. Falls back to direct string building if
Jinja2 is not available.
"""

import logging
import os
from datetime import datetime

from core.models import WebsiteReport, PageData

logger = logging.getLogger(__name__)


def generate_markdown_report(report: WebsiteReport, output_dir: str) -> str:
    """
    Generate a Markdown report for a crawled website.

    Args:
        report: The completed crawl report.
        output_dir: Directory to write the file into.

    Returns:
        Absolute path to the written Markdown file.
    """
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, "report.md")

    md = _build_markdown(report)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md)

    size_kb = os.path.getsize(filepath) / 1024
    logger.info(f"[markdown] 📝 Generated {filepath} ({size_kb:.1f} KB)")
    return filepath


def _build_markdown(report: WebsiteReport) -> str:
    """Build the full Markdown report string."""
    lines: list[str] = []

    # ── Header ──
    lines.append(f"# Website Report — {report.domain}")
    lines.append("")
    lines.append(f"**Crawled On:** {_format_datetime(report.crawl_started)}")
    lines.append(f"**Total Pages Crawled:** {report.total_pages_crawled}")
    if report.site_title:
        lines.append(f"**Site Title:** {report.site_title}")
    if report.language:
        lines.append(f"**Language:** {report.language}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Table of Contents ──
    lines.append("## Table of Contents")
    lines.append("")
    lines.append("1. [Website Overview](#1-website-overview)")
    lines.append("2. [Contact Information](#2-contact-information)")
    lines.append("3. [Site Structure](#3-site-structure)")
    lines.append("4. [Page Details](#4-page-details)")
    lines.append("5. [All Pages Index](#5-all-pages-index)")
    if report.failed_urls:
        lines.append("6. [Failed URLs](#6-failed-urls)")
    if report.external_links:
        lines.append("7. [External Links](#7-external-links)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── 1. Website Overview ──
    lines.append("## 1. Website Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| **Domain** | {report.domain} |")
    lines.append(f"| **Base URL** | {report.base_url} |")
    lines.append(f"| **Total Pages Crawled** | {report.total_pages_crawled} |")
    lines.append(f"| **Failed Pages** | {report.failed_pages} |")
    lines.append(f"| **Total URLs Discovered** | {report.total_urls_found} |")
    lines.append(f"| **Total Words** | {report.total_words:,} |")
    lines.append(f"| **Total Images** | {report.total_images} |")
    lines.append(f"| **Crawl Started** | {_format_datetime(report.crawl_started)} |")
    lines.append(f"| **Crawl Finished** | {_format_datetime(report.crawl_finished)} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── 2. Contact Information ──
    lines.append("## 2. Contact Information")
    lines.append("")
    has_contact = False

    if report.all_emails:
        has_contact = True
        lines.append("### Emails")
        for email in report.all_emails:
            lines.append(f"- {email}")
        lines.append("")

    if report.all_phones:
        has_contact = True
        lines.append("### Phone Numbers")
        for phone in report.all_phones:
            lines.append(f"- {phone}")
        lines.append("")

    if report.all_addresses:
        has_contact = True
        lines.append("### Addresses")
        for addr in report.all_addresses:
            lines.append(f"- {addr}")
        lines.append("")

    if report.social_media:
        has_contact = True
        lines.append("### Social Media")
        for platform, url in sorted(report.social_media.items()):
            lines.append(f"- **{platform.title()}:** {url}")
        lines.append("")

    if not has_contact:
        lines.append("*No contact information found.*")
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── 3. Site Structure ──
    lines.append("## 3. Site Structure")
    lines.append("")

    # Group pages by depth
    by_depth: dict[int, list[PageData]] = {}
    for page in report.pages:
        by_depth.setdefault(page.depth, []).append(page)

    for depth in sorted(by_depth.keys()):
        pages = by_depth[depth]
        label = "Homepage" if depth == 0 else f"Depth {depth} ({len(pages)} pages)"
        lines.append(f"### {label}")
        lines.append("")
        for page in pages:
            title = page.page_title or "(No title)"
            lines.append(f"- [{title}]({page.url})")
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── 4. Page Details ──
    lines.append("## 4. Page Details")
    lines.append("")

    for i, page in enumerate(report.pages, 1):
        title = page.page_title or "(No title)"
        lines.append(f"### 4.{i}. {title}")
        lines.append("")
        lines.append(f"**URL:** {page.url}")
        lines.append(f"**Words:** {page.word_count} | **Images:** {len(page.images)} | **Depth:** {page.depth}")
        lines.append("")

        # Headings summary
        if page.headings:
            lines.append("**Headings:**")
            for level, headings_list in page.headings.items():
                for h in headings_list[:5]:  # limit to 5 per level
                    lines.append(f"- *{level}:* {h}")
            lines.append("")

        # First few paragraphs as summary
        if page.paragraphs:
            lines.append("**Content Preview:**")
            preview = page.paragraphs[:3]
            for p in preview:
                # Truncate long paragraphs
                if len(p) > 300:
                    p = p[:300] + "..."
                lines.append(f"> {p}")
                lines.append(">")
            lines.append("")

        # Contact info found on this page
        if page.emails or page.phones:
            lines.append("**Contact Found:**")
            for email in page.emails:
                lines.append(f"- ✉️ {email}")
            for phone in page.phones:
                lines.append(f"- 📞 {phone}")
            lines.append("")

        # Images
        if page.images:
            lines.append(f"**Images ({len(page.images)}):**")
            lines.append("")
            lines.append("| # | URL | Alt | Title |")
            lines.append("|---|-----|-----|-------|")
            for idx, img in enumerate(page.images, 1):
                src   = img.get("src", "")
                alt   = img.get("alt", "") or ""
                title = img.get("title", "") or ""
                lines.append(f"| {idx} | {src} | {alt} | {title} |")
            lines.append("")

        # Videos
        if page.videos:
            lines.append(f"**Videos ({len(page.videos)}):**")
            for vid_url in page.videos:
                lines.append(f"- {vid_url}")
            lines.append("")

        # Meta
        if page.meta_description:
            lines.append(f"**Meta Description:** {page.meta_description}")
            lines.append("")

        lines.append("---")
        lines.append("")

    # ── 5. All Pages Index ──
    lines.append("## 5. All Pages Index")
    lines.append("")
    lines.append("| # | Title | URL | Words | Images | Depth |")
    lines.append("|---|-------|-----|-------|--------|-------|")

    for i, page in enumerate(report.pages, 1):
        title = (page.page_title or "(No title)")[:60]
        url_short = page.url[:80]
        lines.append(
            f"| {i} | {title} | {url_short} | "
            f"{page.word_count} | {len(page.images)} | {page.depth} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")

    # ── 6. Failed URLs ──
    if report.failed_urls:
        lines.append("## 6. Failed URLs")
        lines.append("")
        lines.append("| URL | Error |")
        lines.append("|-----|-------|")
        for fail in report.failed_urls:
            lines.append(f"| {fail.get('url', '')} | {fail.get('error', '')} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    # ── 7. External Links ──
    if report.external_links:
        lines.append("## 7. External Links")
        lines.append("")
        # Limit to first 100
        for link in report.external_links[:100]:
            lines.append(f"- {link}")
        if len(report.external_links) > 100:
            lines.append(f"- *... and {len(report.external_links) - 100} more*")
        lines.append("")

    # ── Footer ──
    lines.append("---")
    lines.append(f"*Generated by Universal Web Crawler on {_format_datetime(report.crawl_finished)}*")

    return "\n".join(lines)


def _format_datetime(iso_str: str) -> str:
    """Format an ISO datetime string to a readable format."""
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str
