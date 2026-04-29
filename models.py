"""Data models for the job crawler pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class StageResult(Enum):
    """Outcome of a single pipeline stage."""
    SUCCESS = "success"
    FAILED = "failed"
    DISABLED = "disabled"
    SKIPPED = "skipped"


@dataclass
class CrawlAttempt:
    """Record of a single stage attempt."""
    stage_name: str
    result: StageResult
    status_code: Optional[int] = None
    content_length: int = 0
    error_message: Optional[str] = None
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CrawlResponse:
    """Unified response from any pipeline stage."""
    success: bool
    html: str = ""
    markdown: str = ""
    text: str = ""
    status_code: int = 0
    stage_name: str = ""
    attempts: list[CrawlAttempt] = field(default_factory=list)
    url: str = ""

    @property
    def content(self) -> str:
        """Return the best available content (prefer markdown > text > html)."""
        return self.markdown or self.text or self.html


@dataclass
class JobListing:
    """A single parsed job listing."""
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    job_type: str = ""          # Full-time, Part-time, Contract
    experience_level: str = ""  # Junior, Mid, Senior
    date_posted: str = ""
    salary: str = ""
    department: str = ""
    is_it_role: bool = False
    source_url: str = ""
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __str__(self) -> str:
        parts = [f"📋 {self.title}"]
        if self.company:
            parts.append(f"   🏢 {self.company}")
        if self.location:
            parts.append(f"   📍 {self.location}")
        if self.job_type:
            parts.append(f"   💼 {self.job_type}")
        if self.salary:
            parts.append(f"   💰 {self.salary}")
        if self.url:
            parts.append(f"   🔗 {self.url}")
        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# Universal Crawler models (new — does NOT affect legacy JobListing flow)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class PageData:
    """Extracted content from a single crawled page."""

    # Identity
    url: str = ""
    page_title: str = ""
    crawled_at: str = field(default_factory=lambda: datetime.now().isoformat())
    depth: int = 0

    # Content
    headings: dict = field(default_factory=dict)       # {"h1": [...], "h2": [...], ...}
    paragraphs: list[str] = field(default_factory=list)
    lists: list = field(default_factory=list)           # [[items], [items], ...]
    tables: list = field(default_factory=list)           # [{"headers": [], "rows": []}]

    # Media
    images: list[dict] = field(default_factory=list)     # [{"src": "", "alt": "", "title": ""}]
    videos: list[str] = field(default_factory=list)

    # Links
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)

    # Contact info (populated by contact_extractor)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    social_links: dict = field(default_factory=dict)     # {"twitter": "...", "linkedin": "..."}

    # Meta / SEO
    meta_description: str = ""
    meta_keywords: str = ""
    og_title: str = ""
    og_image: str = ""
    og_description: str = ""
    canonical_url: str = ""
    language: str = ""
    structured_data: list[dict] = field(default_factory=list)  # JSON-LD entries

    # Raw
    raw_text: str = ""
    word_count: int = 0
    fetch_stage: str = ""       # which pipeline stage succeeded


@dataclass
class WebsiteReport:
    """Complete crawl result for an entire website."""

    # Site identity
    domain: str = ""
    base_url: str = ""
    site_title: str = ""
    platform: str = "unknown"
    language: str = ""

    # Timing
    crawl_started: str = ""
    crawl_finished: str = ""
    crawl_stopped_reason: str = ""

    # Stats
    total_urls_found: int = 0
    total_pages_crawled: int = 0
    failed_pages: int = 0
    total_words: int = 0
    total_images: int = 0

    # All page data
    pages: list[PageData] = field(default_factory=list)

    # Aggregated global data
    all_emails: list[str] = field(default_factory=list)
    all_phones: list[str] = field(default_factory=list)
    all_addresses: list[str] = field(default_factory=list)
    social_media: dict = field(default_factory=dict)
    navigation_links: list[str] = field(default_factory=list)

    # Failures & external refs
    failed_urls: list[dict] = field(default_factory=list)   # [{"url": "", "error": ""}]
    external_links: list[str] = field(default_factory=list)