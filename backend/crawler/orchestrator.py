"""
Crawl orchestrator — the main crawl loop.

Coordinates URL discovery, fetching, extraction, and output generation
for a full-site crawl. Reuses the existing CrawlPipeline for per-page fetching.
"""

import asyncio
import logging
import re
import time
from datetime import datetime

from backend.core.config import PipelineConfig
from backend.core.models import CrawlResponse, PageData, WebsiteReport
from backend.core.pipeline import CrawlPipeline

from backend.crawler.url_queue import URLQueue, QueueEntry
from backend.crawler.checkpoint import CrawlCheckpoint
from backend.discovery.robots_parser import RobotsChecker
from backend.discovery.sitemap_parser import SitemapParser
from backend.discovery.link_extractor import extract_internal_links
from backend.discovery.nav_discovery import discover_nav_links
from backend.discovery.url_utils import (
    normalize_url,
    extract_base_domain,
    classify_url_priority,
)
from backend.extraction.content_extractor import ContentExtractor
from backend.extraction.contact_extractor import extract_contact_info
from backend.extraction.meta_extractor import extract_meta
from backend.paths import CHECKPOINT_FILE

logger = logging.getLogger(__name__)

# ── Platform detection signals ─────────────────────────────────────────
_PLATFORM_META_PATTERNS = {
    "wordpress": re.compile(r"wordpress", re.I),
    "joomla": re.compile(r"joomla", re.I),
    "drupal": re.compile(r"drupal", re.I),
    "shopify": re.compile(r"shopify", re.I),
    "wix": re.compile(r"wix\.com", re.I),
    "squarespace": re.compile(r"squarespace", re.I),
    "webflow": re.compile(r"webflow", re.I),
}

_PLATFORM_HTML_SIGNALS = {
    "wordpress": ["/wp-content/", "/wp-json/", "/wp-includes/"],
    "shopify": [".myshopify.com", "cdn.shopify.com"],
    "wix": ["static.wixstatic.com", "wix.com/"],
    "webflow": ["webflow.js", "assets.website-files.com"],
    "squarespace": ["squarespace.com/", "static1.squarespace.com"],
}


class CrawlOrchestrator:
    """
    Universal website crawler.

    Takes a homepage URL and crawls the entire site:
    1. Checks robots.txt
    2. Discovers URLs from sitemaps
    2.5. Interactive navigation discovery (for SPA sites)
    3. Crawls each page via the existing 6-stage pipeline (true async concurrency)
    4. Extracts universal content (text, links, media, contact, meta)
    5. Discovers new links from each page
    6. Aggregates into a WebsiteReport
    """

    def __init__(
        self,
        max_pages: int = 200,
        max_discovered_urls: int = 1000,
        max_depth: int = 5,
        max_time_minutes: int = 30,
        request_delay: float = 1.5,
        max_concurrent: int = 3,
        respect_robots: bool = True,
        checkpoint_every: int = 25,
        checkpoint_file: str = str(CHECKPOINT_FILE),
        pipeline_config: PipelineConfig | None = None,
    ):
        self.max_pages = max_pages
        self.max_discovered_urls = max_discovered_urls
        self.max_depth = max_depth
        self.max_time_seconds = max_time_minutes * 60
        self.request_delay = request_delay
        self.max_concurrent = max_concurrent
        self.respect_robots = respect_robots
        self.checkpoint_every = checkpoint_every

        # Sub-components
        self._pipeline = CrawlPipeline(pipeline_config or PipelineConfig())
        self._queue = URLQueue(
            max_discovered_urls=max_discovered_urls,
            max_depth=max_depth,
        )
        self._checkpoint = CrawlCheckpoint(checkpoint_file)
        self._extractor = ContentExtractor()

        # Concurrency controls
        self._active_entries: dict[asyncio.Task, QueueEntry] = {}
        self._rate_limit_lock = asyncio.Lock()
        self._last_request_started: float = 0.0

        # State
        self._completed_pages: list[PageData] = []
        self._failed_urls: list[dict] = []
        self._base_domain: str = ""
        self._base_url: str = ""
        self._crawl_started: str = ""
        self._robots: RobotsChecker | None = None
        self._navigation_links: set[str] = set()
        self._detected_platform: str = "unknown"
        self._stopped_active_entries: list[dict] = []

    async def crawl(
        self,
        url: str,
        resume: bool = False,
        progress_callback=None,
    ) -> WebsiteReport:
        """
        Crawl an entire website starting from the given URL.

        Args:
            url: Homepage or starting URL.
            resume: If True, try to resume from a checkpoint.
            progress_callback: Optional callable(completed, total, current_url)
                               for progress reporting.

        Returns:
            WebsiteReport with all crawled data.
        """
        self._crawl_started = datetime.now().isoformat()
        start_time = time.perf_counter()

        # Normalize and extract domain
        self._base_url = normalize_url(url)
        self._base_domain = extract_base_domain(self._base_url)

        # Set domain scope on the queue
        self._queue.set_allowed_domain(self._base_domain)

        logger.info(f"{'='*60}")
        logger.info(f"🌐 Universal Crawler — Starting")
        logger.info(f"   URL: {self._base_url}")
        logger.info(f"   Domain: {self._base_domain}")
        logger.info(f"   Max pages: {self.max_pages}")
        logger.info(f"   Max discovered URLs: {self.max_discovered_urls}")
        logger.info(f"   Max depth: {self.max_depth}")
        logger.info(f"{'='*60}")

        # ── Resume from checkpoint? ──
        resume_succeeded = False

        if resume and self._checkpoint.exists():
            restored = self._checkpoint.load()
            if restored and restored.get("domain") == self._base_domain:
                self._completed_pages = restored["pages"]
                self._failed_urls = restored["failed_urls"]
                self._crawl_started = restored.get("crawl_started", self._crawl_started)
                self._navigation_links = set(restored.get("navigation_links", []))
                self._detected_platform = restored.get("detected_platform", "unknown")

                restore_entries = restored.get("pending_queue", [])
                if restore_entries:
                    # v2 checkpoint — true continuation
                    self._queue.restore(
                        seen_urls=restored["seen_urls"],
                        pending_entries=restore_entries,
                        completed_count=len(self._completed_pages),
                    )
                else:
                    # v1 fallback — approximate but avoids recrawling completed pages
                    completed = {normalize_url(p.url) for p in self._completed_pages}
                    pending = [
                        {"url": u, "depth": 1, "priority": classify_url_priority(u)}
                        for u in restored["seen_urls"]
                        if normalize_url(u) not in completed
                    ]
                    self._queue.restore(
                        seen_urls=set(restored["seen_urls"]),
                        pending_entries=pending,
                        completed_count=len(self._completed_pages),
                    )

                resume_succeeded = True
                logger.info(
                    f"[resume] ♻️  Resumed: {len(self._completed_pages)} pages, "
                    f"{len(restored['seen_urls'])} seen URLs, "
                    f"{self._queue.pending} pending"
                )

        if not resume_succeeded:
            # ── Phase 1: Robots.txt ──
            if self.respect_robots:
                self._robots = await RobotsChecker.from_url(self._base_url)

                # Use robots.txt crawl-delay if specified and higher than our default
                robots_delay = self._robots.crawl_delay
                if robots_delay and robots_delay > self.request_delay:
                    logger.info(f"[robots] ⏱️  Using crawl-delay: {robots_delay}s")
                    self.request_delay = robots_delay

            # ── Phase 2: Sitemap discovery ──
            sitemap_urls: list[str] = []
            robot_sitemaps = self._robots.sitemaps if self._robots else []

            parser = SitemapParser(self._base_url)
            sitemap_urls = await parser.discover(extra_sitemap_urls=robot_sitemaps)

            # Seed queue with sitemap URLs (priority 1 = high)
            if sitemap_urls:
                added = await self._queue.add_batch(
                    sitemap_urls, depth=1, priority=1,
                )
                logger.info(f"[sitemap] Added {added} URLs from sitemaps")

            # Always seed the homepage (priority 0 = highest)
            await self._queue.add(self._base_url, depth=0, priority=0)

            # ── Phase 2.5: Interactive navigation discovery (for SPA sites) ──
            # Only runs when sitemap yielded few/no URLs — indicates the site
            # may be a JavaScript SPA with no standard link discovery path.
            if len(sitemap_urls) < 5:
                logger.info(
                    "[nav_discovery] Sitemap yielded few URLs, "
                    "attempting interactive navigation discovery..."
                )
                try:
                    nav_urls = await discover_nav_links(
                        self._base_url,
                        timeout=self._pipeline.config.timeout,
                    )
                    if nav_urls:
                        self._navigation_links.update(nav_urls)
                        added = await self._queue.add_batch(
                            nav_urls, depth=1, priority=1,
                        )
                        logger.info(
                            f"[nav_discovery] Added {added} URLs from "
                            f"interactive navigation"
                        )
                    else:
                        logger.info("[nav_discovery] No additional URLs found")
                except Exception as e:
                    logger.warning(f"[nav_discovery] Failed: {e}")

        # ── Phase 3+4: Concurrent crawl loop ──
        stop_reason = await self._run_crawl_loop(start_time, progress_callback)

        # ── Phase 5: Aggregate into report ──
        crawl_finished = datetime.now().isoformat()
        report = self._build_report(crawl_finished, stop_reason)

        # Only delete checkpoint on full completion (queue exhausted)
        if stop_reason == "queue_empty":
            self._checkpoint.delete()
        else:
            self._save_checkpoint(stop_reason)
            logger.info(
                f"[checkpoint] Crawl stopped: {stop_reason}. "
                f"Use --resume to continue."
            )

        elapsed = time.perf_counter() - start_time
        logger.info(f"{'='*60}")
        logger.info(
            f"✅ Crawl complete: {report.total_pages_crawled} pages, "
            f"{report.total_words} words in {elapsed/60:.1f} min "
            f"(stop: {stop_reason})"
        )
        logger.info(f"{'='*60}")

        return report

    # ── Concurrency and rate limiting ──────────────────────────────────

    async def _respect_rate_limit(self) -> None:
        """Enforce minimum delay between request starts across all tasks."""
        if self.request_delay <= 0:
            return
        async with self._rate_limit_lock:
            now = time.perf_counter()
            wait_for = self.request_delay - (now - self._last_request_started)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_request_started = time.perf_counter()

    async def _run_crawl_loop(
        self,
        start_time: float,
        progress_callback=None,
    ) -> str:
        """
        Bounded async task fanout crawl loop.

        Returns the stop reason: "queue_empty", "page_limit", or "time_budget".
        """
        active_tasks: set[asyncio.Task] = set()
        stop_reason = "queue_empty"
        self._stopped_active_entries = []

        while True:
            # ── Check stop conditions ──
            if len(self._completed_pages) >= self.max_pages:
                stop_reason = "page_limit"
                break

            elapsed = time.perf_counter() - start_time
            if elapsed >= self.max_time_seconds:
                stop_reason = "time_budget"
                break

            # ── Fill up to max_concurrent tasks ──
            while (
                len(active_tasks) < self.max_concurrent
                and len(self._completed_pages) + len(active_tasks) < self.max_pages
            ):
                entry = await self._queue.get()
                if entry is None:
                    break  # queue empty

                # Robots check — skip without rate-limiting delay
                if self._robots and not self._robots.is_allowed(entry.url):
                    logger.debug(f"[robots] 🚫 Blocked by robots.txt: {entry.url}")
                    self._queue.mark_done()
                    continue

                # Launch task with rate-limiting
                async def _process_entry(e: QueueEntry = entry) -> PageData | None:
                    await self._respect_rate_limit()
                    return await self._process_page(e.url, e.depth)

                task = asyncio.create_task(_process_entry())
                self._active_entries[task] = entry
                active_tasks.add(task)

            # If no active tasks and queue is empty, we're done
            if not active_tasks:
                stop_reason = "queue_empty"
                break

            # ── Wait for at least one task to complete or for time budget expiry ──
            remaining_time = self.max_time_seconds - (time.perf_counter() - start_time)
            if remaining_time <= 0:
                stop_reason = "time_budget"
                break

            done, active_tasks = await asyncio.wait(
                active_tasks,
                timeout=remaining_time,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                stop_reason = "time_budget"
                break

            for task in done:
                entry = self._active_entries.pop(task)
                page_data = task.result()
                self._queue.mark_done()
                await self._handle_completed_page(
                    entry,
                    page_data,
                    progress_callback,
                )

        # Cancel remaining in-flight tasks on time-budget/page-limit shutdown.
        # Save their entries so they resume later instead of being counted done.
        if active_tasks:
            for task in active_tasks:
                entry = self._active_entries.pop(task, None)
                if entry:
                    self._stopped_active_entries.append({
                        "url": entry.url,
                        "depth": entry.depth,
                        "priority": entry.priority,
                    })
                    self._queue.mark_abandoned()
                task.cancel()
            await asyncio.gather(*active_tasks, return_exceptions=True)

        return stop_reason

    async def _handle_completed_page(
        self,
        entry: QueueEntry,
        page_data: PageData | None,
        progress_callback=None,
    ) -> None:
        """Handle completed page data consistently for normal and edge paths."""
        if page_data:
            self._completed_pages.append(page_data)

            # Discover new links — per-link priority (A3 fix)
            for link in page_data.internal_links:
                await self._queue.add(
                    link,
                    depth=entry.depth + 1,
                    priority=classify_url_priority(link),
                )

        if progress_callback:
            progress_callback(
                len(self._completed_pages),
                self._queue.stats["seen"],
                entry.url,
            )

        # Periodic checkpoint (B1 fix: guard > 0)
        if (
            self.checkpoint_every > 0
            and len(self._completed_pages) > 0
            and len(self._completed_pages) % self.checkpoint_every == 0
        ):
            self._save_checkpoint()

    # ── Checkpoint helper ──────────────────────────────────────────────

    def _save_checkpoint(self, stop_reason: str = "") -> None:
        """Save current crawl state to disk (v2 schema)."""
        active_entries = [
            {"url": e.url, "depth": e.depth, "priority": e.priority}
            for e in self._active_entries.values()
        ]
        active_entries.extend(self._stopped_active_entries)
        self._checkpoint.save(
            completed_pages=self._completed_pages,
            seen_urls=self._queue.seen_urls,
            failed_urls=self._failed_urls,
            base_url=self._base_url,
            domain=self._base_domain,
            crawl_started=self._crawl_started,
            pending_queue=self._queue.snapshot(),
            active_queue=active_entries,
            queue_stats=self._queue.stats,
            crawl_stopped_reason=stop_reason,
            navigation_links=sorted(self._navigation_links),
            detected_platform=self._detected_platform,
        )

    # ── Page processing ────────────────────────────────────────────────

    async def _process_page(self, url: str, depth: int) -> PageData | None:
        """Fetch a single page and extract its content."""
        logger.info(
            f"[{len(self._completed_pages)+1}] "
            f"Crawling (d={depth}): {url}"
        )

        try:
            # Use existing 6-stage pipeline for fetching
            response: CrawlResponse = await self._pipeline.crawl(url)

            if not response.success:
                self._failed_urls.append({
                    "url": url,
                    "error": f"All stages failed",
                    "depth": depth,
                })
                return None

            html = response.html or ""
            raw_text = response.content or ""
            markdown_text = response.markdown or ""

            # Content extraction
            page = self._extractor.extract(
                html=html,
                url=url,
                depth=depth,
                fetch_stage=response.stage_name,
                markdown_text=markdown_text,
            )

            # If content extractor got no raw_text but pipeline has text, use that
            if not page.raw_text and raw_text:
                page.raw_text = raw_text
                page.word_count = len(raw_text.split())

            # Contact info
            contact = extract_contact_info(html, raw_text)
            page.emails = contact["emails"]
            page.phones = contact["phones"]
            page.addresses = contact["addresses"]
            page.social_links = contact["social_links"]

            # Meta / SEO (B4 fix: assign og_description)
            meta = extract_meta(html)
            page.page_title = page.page_title or meta["page_title"]
            page.meta_description = meta["meta_description"]
            page.meta_keywords = meta["meta_keywords"]
            page.og_title = meta["og_title"]
            page.og_image = meta["og_image"]
            page.og_description = meta.get("og_description", "")
            page.canonical_url = meta["canonical_url"]
            page.language = meta["language"]
            page.structured_data = meta["structured_data"]

            # Platform detection needs the original HTML, not stripped raw_text.
            if self._detected_platform == "unknown":
                self._detected_platform = self._detect_platform_from_html(
                    html,
                    page.structured_data,
                )

            return page

        except Exception as e:
            logger.error(f"[crawl] ❌ Error processing {url}: {e}")
            self._failed_urls.append({
                "url": url,
                "error": str(e),
                "depth": depth,
            })
            return None

    # ── Platform detection ─────────────────────────────────────────────

    def _detect_platform_from_html(
        self,
        html: str,
        structured_data: list[dict] | None = None,
    ) -> str:
        """
        Detect the CMS/platform from original page HTML and JSON-LD.

        Checks <meta name="generator">, known URL/path signals, and
        structured data. No network calls.
        """
        html = html or ""

        # Check meta generator tag.
        generator_match = re.search(
            r'<meta\s+[^>]*(?:name|property)=["\']generator["\'][^>]*'
            r'content=["\']([^"\']+)["\']',
            html,
            re.I,
        )
        if not generator_match:
            generator_match = re.search(
                r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*'
                r'(?:name|property)=["\']generator["\']',
                html,
                re.I,
            )
        if generator_match:
            generator = generator_match.group(1).lower()
            for platform, pattern in _PLATFORM_META_PATTERNS.items():
                if pattern.search(generator):
                    return platform

        # Check HTML for known path/domain signals.
        html_lower = html.lower()
        for platform, signals in _PLATFORM_HTML_SIGNALS.items():
            for signal in signals:
                if signal.lower() in html_lower:
                    return platform

        # Check structured data.
        for sd in structured_data or []:
            sd_str = str(sd).lower()
            for platform in _PLATFORM_META_PATTERNS:
                if platform in sd_str:
                    return platform

        return "unknown"

    def _detect_platform(self) -> str:
        """Return the platform detected while processing original page HTML."""
        return self._detected_platform

    # ── Report building ────────────────────────────────────────────────

    def _build_report(self, crawl_finished: str, stop_reason: str) -> WebsiteReport:
        """Aggregate all page data into a WebsiteReport."""

        # Aggregate global contact info
        all_emails: set[str] = set()
        all_phones: set[str] = set()
        all_addresses: list[str] = []
        social_media: dict[str, str] = {}
        all_external: set[str] = set()
        total_words = 0
        total_images = 0
        site_title = ""
        language = ""

        for page in self._completed_pages:
            all_emails.update(page.emails)
            all_phones.update(page.phones)
            all_addresses.extend(page.addresses)
            all_external.update(page.external_links)
            total_words += page.word_count
            total_images += len(page.images)

            # Merge social links (first occurrence wins)
            for platform, link in page.social_links.items():
                if platform not in social_media:
                    social_media[platform] = link

            # Use homepage title as site title
            if page.depth == 0 and page.page_title:
                site_title = page.page_title

            # Use homepage language
            if page.depth == 0 and page.language:
                language = page.language

        return WebsiteReport(
            domain=self._base_domain,
            base_url=self._base_url,
            site_title=site_title,
            platform=self._detect_platform(),
            language=language,
            crawl_started=self._crawl_started,
            crawl_finished=crawl_finished,
            crawl_stopped_reason=stop_reason,
            total_urls_found=self._queue.stats["seen"],
            total_pages_crawled=len(self._completed_pages),
            failed_pages=len(self._failed_urls),
            total_words=total_words,
            total_images=total_images,
            pages=self._completed_pages,
            all_emails=sorted(all_emails),
            all_phones=sorted(all_phones),
            all_addresses=list(dict.fromkeys(all_addresses)),  # dedup preserve order
            social_media=social_media,
            navigation_links=sorted(self._navigation_links),
            failed_urls=self._failed_urls,
            external_links=sorted(all_external),
        )

