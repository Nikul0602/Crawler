"""
Crawl orchestrator — the main crawl loop.

Coordinates URL discovery, fetching, extraction, and output generation
for a full-site crawl. Reuses the existing CrawlPipeline for per-page fetching.
"""

import asyncio
import logging
import time
from datetime import datetime

from config import PipelineConfig
from models import CrawlResponse, PageData, WebsiteReport
from pipeline import CrawlPipeline

from crawler.url_queue import URLQueue
from crawler.checkpoint import CrawlCheckpoint
from discovery.robots_parser import RobotsChecker
from discovery.sitemap_parser import SitemapParser
from discovery.link_extractor import extract_internal_links
from discovery.nav_discovery import discover_nav_links
from discovery.url_utils import (
    normalize_url,
    extract_base_domain,
    classify_url_priority,
)
from extraction.content_extractor import ContentExtractor
from extraction.contact_extractor import extract_contact_info
from extraction.meta_extractor import extract_meta

logger = logging.getLogger(__name__)


class CrawlOrchestrator:
    """
    Universal website crawler.

    Takes a homepage URL and crawls the entire site:
    1. Checks robots.txt
    2. Discovers URLs from sitemaps
    3. Crawls each page via the existing 6-stage pipeline
    4. Extracts universal content (text, links, media, contact, meta)
    5. Discovers new links from each page
    6. Aggregates into a WebsiteReport
    """

    def __init__(
        self,
        max_pages: int = 200,
        max_depth: int = 5,
        max_time_minutes: int = 30,
        request_delay: float = 1.5,
        max_concurrent: int = 3,
        respect_robots: bool = True,
        checkpoint_every: int = 25,
        checkpoint_file: str = "crawl_state.json",
        pipeline_config: PipelineConfig | None = None,
    ):
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.max_time_seconds = max_time_minutes * 60
        self.request_delay = request_delay
        self.max_concurrent = max_concurrent
        self.respect_robots = respect_robots
        self.checkpoint_every = checkpoint_every

        # Sub-components
        self._pipeline = CrawlPipeline(pipeline_config or PipelineConfig())
        self._queue = URLQueue(max_pages=max_pages, max_depth=max_depth)
        self._checkpoint = CrawlCheckpoint(checkpoint_file)
        self._extractor = ContentExtractor()
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # State
        self._completed_pages: list[PageData] = []
        self._failed_urls: list[dict] = []
        self._base_domain: str = ""
        self._base_url: str = ""
        self._crawl_started: str = ""
        self._robots: RobotsChecker | None = None

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

        logger.info(f"{'='*60}")
        logger.info(f"🌐 Universal Crawler — Starting")
        logger.info(f"   URL: {self._base_url}")
        logger.info(f"   Domain: {self._base_domain}")
        logger.info(f"   Max pages: {self.max_pages}")
        logger.info(f"   Max depth: {self.max_depth}")
        logger.info(f"{'='*60}")

        # ── Resume from checkpoint? ──
        if resume and self._checkpoint.exists():
            restored = self._checkpoint.load()
            if restored and restored.get("domain") == self._base_domain:
                self._completed_pages = restored["pages"]
                self._failed_urls = restored["failed_urls"]
                self._crawl_started = restored.get("crawl_started", self._crawl_started)

                # Re-seed the seen set so we don't re-crawl
                for seen_url in restored["seen_urls"]:
                    await self._queue.add(seen_url, depth=0, priority=99)
                    # Mark as seen (they won't actually be queued since we'll
                    # add them again and they'll be deduped)

                logger.info(
                    f"[resume] ♻️  Resumed: {len(self._completed_pages)} pages, "
                    f"{len(restored['seen_urls'])} seen URLs"
                )

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

        # ── Phase 3+4: Crawl loop ──
        while not self._queue.is_empty:
            # Time budget check
            elapsed = time.perf_counter() - start_time
            if elapsed >= self.max_time_seconds:
                logger.warning(
                    f"[crawl] ⏱️  Time budget exhausted ({elapsed/60:.1f} min)"
                )
                break

            entry = await self._queue.get()
            if entry is None:
                break

            # Robots check
            if self._robots and not self._robots.is_allowed(entry.url):
                logger.debug(f"[robots] 🚫 Blocked by robots.txt: {entry.url}")
                self._queue.mark_done()
                continue

            # Fetch + extract
            async with self._semaphore:
                page_data = await self._process_page(entry.url, entry.depth)

            if page_data:
                self._completed_pages.append(page_data)

                # Discover new links from this page
                new_links = page_data.internal_links
                added = await self._queue.add_batch(
                    new_links,
                    depth=entry.depth + 1,
                    priority=classify_url_priority(entry.url),
                )
                if added > 0:
                    logger.debug(f"[links] +{added} new URLs from {entry.url}")

            self._queue.mark_done()

            # Progress callback
            if progress_callback:
                progress_callback(
                    len(self._completed_pages),
                    self._queue.stats["seen"],
                    entry.url,
                )

            # Checkpoint
            if (
                self.checkpoint_every > 0
                and len(self._completed_pages) % self.checkpoint_every == 0
            ):
                self._checkpoint.save(
                    self._completed_pages,
                    self._queue.seen_urls,
                    self._failed_urls,
                    self._base_url,
                    self._base_domain,
                    self._crawl_started,
                )

            # Rate limiting
            await asyncio.sleep(self.request_delay)

        # ── Phase 5: Aggregate into report ──
        crawl_finished = datetime.now().isoformat()
        report = self._build_report(crawl_finished)

        # Clean up checkpoint on successful completion
        self._checkpoint.delete()

        elapsed = time.perf_counter() - start_time
        logger.info(f"{'='*60}")
        logger.info(
            f"✅ Crawl complete: {report.total_pages_crawled} pages, "
            f"{report.total_words} words in {elapsed/60:.1f} min"
        )
        logger.info(f"{'='*60}")

        return report

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

            # Meta / SEO
            meta = extract_meta(html)
            page.page_title = page.page_title or meta["page_title"]
            page.meta_description = meta["meta_description"]
            page.meta_keywords = meta["meta_keywords"]
            page.og_title = meta["og_title"]
            page.og_image = meta["og_image"]
            page.canonical_url = meta["canonical_url"]
            page.language = meta["language"]
            page.structured_data = meta["structured_data"]

            return page

        except Exception as e:
            logger.error(f"[crawl] ❌ Error processing {url}: {e}")
            self._failed_urls.append({
                "url": url,
                "error": str(e),
                "depth": depth,
            })
            return None

    def _build_report(self, crawl_finished: str) -> WebsiteReport:
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
            language=language,
            crawl_started=self._crawl_started,
            crawl_finished=crawl_finished,
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
            failed_urls=self._failed_urls,
            external_links=sorted(all_external),
        )
