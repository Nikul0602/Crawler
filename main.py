"""
Main entry point — Universal Website Crawler + Legacy IT Job Crawler.

Usage (Universal Crawl — default):
    python main.py https://example.com
    python main.py https://example.com --max-pages 500
    python main.py https://example.com --max-depth 3 --output ./reports
    python main.py https://example.com --resume

Usage (Legacy IT Job Crawl):
    python main.py https://company.com/careers --mode jobs
    python main.py https://company.com/careers --mode jobs --all-jobs
"""

import asyncio
import argparse
import logging
import json
import os
import sys
from datetime import datetime

from config import PipelineConfig, CrawlerConfig


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Universal Website Crawler + IT Job Crawler"
    )
    parser.add_argument(
        "url",
        help="Website URL to crawl (homepage for universal, career page for jobs)",
    )

    # ── Mode selector ──
    parser.add_argument(
        "--mode",
        choices=["crawl", "jobs"],
        default="crawl",
        help="Crawl mode: 'crawl' (universal, default) or 'jobs' (legacy IT job crawler)",
    )

    # ── Universal crawl options ──
    crawl_group = parser.add_argument_group("Universal Crawl Options")
    crawl_group.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Maximum pages to crawl (default: 200)",
    )
    crawl_group.add_argument(
        "--max-depth",
        type=int,
        default=5,
        help="Maximum link-follow depth (default: 5)",
    )
    crawl_group.add_argument(
        "--max-time",
        type=int,
        default=30,
        help="Time budget in minutes (default: 30)",
    )
    crawl_group.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Delay between requests in seconds (default: 1.5)",
    )
    crawl_group.add_argument(
        "--concurrent",
        type=int,
        default=3,
        help="Max concurrent requests (default: 3)",
    )
    crawl_group.add_argument(
        "--no-robots",
        action="store_true",
        help="Ignore robots.txt restrictions",
    )
    crawl_group.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted crawl from checkpoint",
    )
    crawl_group.add_argument(
        "--output",
        dest="output_dir",
        default="./output",
        help="Output directory for reports (default: ./output)",
    )
    crawl_group.add_argument(
        "--format",
        nargs="+",
        choices=["markdown", "json"],
        default=["markdown", "json"],
        help="Output formats (default: markdown json)",
    )

    # ── Legacy job crawler options ──
    jobs_group = parser.add_argument_group("Legacy Job Crawler Options")
    jobs_group.add_argument(
        "--disable-browser",
        action="store_true",
        help="[jobs mode] Disable browser-based stages (Crawl4AI + Scrapling)",
    )
    jobs_group.add_argument(
        "--disable-jina",
        action="store_true",
        help="[jobs mode] Disable Jina reader proxy stage",
    )
    jobs_group.add_argument(
        "--jina-key",
        default="",
        help="[jobs mode] Jina API key for higher rate limits",
    )
    jobs_group.add_argument(
        "--all-jobs",
        action="store_true",
        help="[jobs mode] Show all jobs, not just IT roles",
    )
    jobs_group.add_argument(
        "--jobs-output",
        choices=["text", "json"],
        default="text",
        help="[jobs mode] Output format (default: text)",
    )

    # ── Global options ──
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout per page fetch in seconds (default: 30)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )

    return parser.parse_args()


# ═══════════════════════════════════════════════════════════════════════
# Universal Crawl Mode
# ═══════════════════════════════════════════════════════════════════════


async def run_universal_crawl(args):
    """Run the universal website crawler."""
    from crawler.orchestrator import CrawlOrchestrator
    from output.markdown_report import generate_markdown_report
    from output.json_export import export_json

    # Build pipeline config
    pipeline_config = PipelineConfig(
        timeout=args.timeout,
    )

    # Build crawler config
    crawler_config = CrawlerConfig(
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        max_time_minutes=args.max_time,
        request_delay=args.delay,
        max_concurrent=args.concurrent,
        respect_robots=not args.no_robots,
        pipeline=pipeline_config,
    )

    # Create orchestrator
    orchestrator = CrawlOrchestrator(
        max_pages=crawler_config.max_pages,
        max_depth=crawler_config.max_depth,
        max_time_minutes=crawler_config.max_time_minutes,
        request_delay=crawler_config.request_delay,
        max_concurrent=crawler_config.max_concurrent,
        respect_robots=crawler_config.respect_robots,
        checkpoint_every=crawler_config.checkpoint_every,
        checkpoint_file=crawler_config.checkpoint_file,
        pipeline_config=pipeline_config,
    )

    # Progress display
    def show_progress(completed, total, current_url):
        url_display = current_url[:60] + "..." if len(current_url) > 60 else current_url
        print(f"\r  [{completed}/{total}] {url_display}        ", end="", flush=True)

    print(f"\n[*] Universal Crawler -- {args.url}")
    print(f"    Max pages: {args.max_pages} | Depth: {args.max_depth} | "
          f"Time limit: {args.max_time} min")
    print(f"    {'Ignoring' if args.no_robots else 'Respecting'} robots.txt")
    if args.resume:
        print(f"    Attempting to resume from checkpoint...")
    print()

    # Run crawl
    report = await orchestrator.crawl(
        url=args.url,
        resume=args.resume,
        progress_callback=show_progress,
    )

    print()  # newline after progress

    # Determine output directory
    output_dir = os.path.join(args.output_dir, report.domain)

    # Generate outputs
    generated_files = []

    if "markdown" in args.format:
        md_path = generate_markdown_report(report, output_dir)
        generated_files.append(md_path)

    if "json" in args.format:
        json_path = export_json(report, output_dir)
        generated_files.append(json_path)

    # Print summary
    print(f"\n{'='*60}")
    print(f"CRAWL SUMMARY -- {report.domain}")
    print(f"{'='*60}")
    print(f"  Pages crawled:  {report.total_pages_crawled}")
    print(f"  Failed pages:   {report.failed_pages}")
    print(f"  Total words:    {report.total_words:,}")
    print(f"  Total images:   {report.total_images}")
    print(f"  Emails found:   {len(report.all_emails)}")
    print(f"  Social links:   {len(report.social_media)}")
    print(f"{'='*60}")

    if generated_files:
        print(f"\nOutput files:")
        for fp in generated_files:
            size_kb = os.path.getsize(fp) / 1024
            print(f"   -> {fp} ({size_kb:.1f} KB)")

    print()


# ═══════════════════════════════════════════════════════════════════════
# Legacy Job Crawl Mode (preserved from original main.py)
# ═══════════════════════════════════════════════════════════════════════


async def run_job_crawl(args):
    """Run the legacy IT job crawler (original behaviour preserved)."""
    from pipeline import CrawlPipeline
    from extraction.job_extractor import ITJobExtractor
    from models import JobListing

    # Build configuration
    config = PipelineConfig(
        timeout=args.timeout,
        enable_crawl4ai=not args.disable_browser,
        enable_scrapling=not args.disable_browser,
        enable_jina=not args.disable_jina,
        jina_api_key=args.jina_key,
    )

    # Create pipeline
    pipeline = CrawlPipeline(config)

    # Crawl the URL
    print(f"\n[*] IT Job Crawler -- {args.url}\n")
    response = await pipeline.crawl(args.url)

    # Print pipeline report
    pipeline.print_report(response)

    if not response.success:
        print("FAILED: Could not fetch the page through all 6 stages.")
        print("   Possible reasons:")
        print("   - The URL is invalid or the site is down")
        print("   - All stages were blocked by anti-bot measures")
        print("   - Network connectivity issues")
        sys.exit(1)

    # Extract jobs
    extractor = ITJobExtractor(config)

    if args.all_jobs:
        all_jobs = extractor.extract(response)
        extractor_all = ITJobExtractor(config)
        if response.html:
            from extraction.job_extractor import ITJobExtractor as IE
            e = IE(config)
            all_raw = e._extract_from_html(response.html, response.url)
            if not all_raw and response.content:
                all_raw = e._extract_from_text(response.content, response.url)
            all_raw = e._deduplicate(all_raw)
            for job in all_raw:
                e._is_it_job(job)
            jobs = all_raw
        else:
            jobs = all_jobs
    else:
        jobs = extractor.extract(response)

    # Output results
    if args.jobs_output == "json":
        _output_json(jobs, response)
    else:
        _output_text(jobs, response, show_all=args.all_jobs)


def _output_text(jobs, response, show_all: bool = False):
    """Print jobs in human-readable format."""
    if not jobs:
        print("No IT job listings found on this page.")
        print("\nTips:")
        print("   - Try with --all-jobs to see all listings")
        print("   - The page might use heavy JavaScript (try enabling browsers)")
        print("   - The URL might not be a job listing page")

        content = response.content[:500]
        if content:
            print(f"\nContent preview (first 500 chars):")
            print(f"{'-'*40}")
            print(content)
            print(f"{'-'*40}")
        return

    print(f"\n{'='*60}")
    if show_all:
        print(f"ALL JOB LISTINGS FOUND: {len(jobs)}")
    else:
        print(f"IT JOB LISTINGS FOUND: {len(jobs)}")
    print(f"{'='*60}\n")

    for i, job in enumerate(jobs, 1):
        if show_all:
            it_marker = " [IT]" if job.is_it_role else ""
            print(f"{i}. {job.title}{it_marker}")
        else:
            print(f"{i}. {job.title}")

        if job.location:
            print(f"   Location: {job.location}")
        if job.department:
            print(f"   Dept: {job.department}")
        if job.job_type:
            print(f"   Type: {job.job_type}")
        if job.url:
            print(f"   URL: {job.url}")
        print()


def _output_json(jobs, response):
    """Print jobs as JSON."""
    from dataclasses import asdict
    output = {
        "source_url": response.url,
        "crawl_stage": response.stage_name,
        "crawled_at": datetime.now().isoformat(),
        "total_jobs": len(jobs),
        "jobs": [asdict(job) for job in jobs],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════


def main():
    args = parse_args()
    setup_logging(args.verbose)

    if args.mode == "jobs":
        asyncio.run(run_job_crawl(args))
    else:
        asyncio.run(run_universal_crawl(args))


if __name__ == "__main__":
    main()