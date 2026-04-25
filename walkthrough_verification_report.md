# Walkthrough Verification Report

**Project:** Universal Web Crawler  
**Verified against:** `walkthrough.md`, full codebase, and current reports in `output/`  
**Date:** 2026-04-25

---

## Executive Verdict

The walkthrough is broadly accurate about the crawler's intended architecture: it has a multi-stage fetch pipeline, sitemap discovery, link discovery, interactive navigation discovery, structured extraction, contact extraction, metadata extraction, checkpointing, and Markdown/JSON export.

However, the walkthrough's strongest marketing-style claim is not valid as a guarantee:

> The crawler does not reliably fetch "every page", "all possible pages", or "A to Z" descriptions from a website.

The actual system is bounded by:

- `max_pages`, default `200`
- `max_depth`, default `5`
- `max_time_minutes`, default `30`
- robots.txt restrictions
- whether URLs are discoverable through sitemaps, links, markdown links, bare URLs, or the limited interactive navigation workflow
- whether the successful fetch stage returns HTML or only markdown/text

The output reports confirm this limitation. One test crawl discovered 200 URLs but crawled only 19 pages, and another reached the 200 URL cap.

---

## Output Report Evidence

| Domain | Report Files | URLs Found | Pages Crawled | Failed Pages | Total Words | Total Images | Main Fetch Stages | Verification Result |
|---|---|---:|---:|---:|---:|---:|---|---|
| `aperions.com` | `output/aperions.com/data.json`, `report1.md` | 29 | 29 | 0 | 20,271 | 928 | `crawl4ai: 29` | Complete within discovered set |
| `goboult.co.in` | `output/goboult.co.in/data.json`, `report2.md` | 200 | 19 | 0 | 19,854 | 482 | `jina_reader: 12`, `crawl4ai: 7` | Not complete; time budget stopped crawl |
| `zynova-solutions.com` | `output/zynova-solutions.com/data.json`, `report.md` | 200 | 199 | 1 | 182,623 | 1,197 | `crawl4ai: 197`, `jina_reader: 2` | Not provably complete; hit default URL/page cap |

Important details:

- `goboult.co.in` ran from `2026-04-25T15:50:06` to `2026-04-25T16:20:24`, about 30.3 minutes. This matches the default time budget and explains why only 19 of 200 discovered URLs were crawled.
- `zynova-solutions.com` found exactly 200 URLs, matching the default `max_pages` cap. Because the queue stops accepting URLs after the cap, this cannot prove all site pages were discovered.
- `aperions.com` found and crawled 29 pages with no failures, so it supports the walkthrough only for a small site that fits inside the crawler limits.

---

## Claim-by-Claim Verification

### 1. "Discovers every page on the website"

**Verdict:** False as written.

The system discovers URLs from several sources, but it does not guarantee complete discovery.

Code evidence:

- `crawler/orchestrator.py` seeds sitemap URLs, then homepage, then follows internal links.
- `crawler/orchestrator.py` stops when the queue is empty, the time budget expires, or page limits are reached.
- `crawler/url_queue.py` rejects URLs after `max_pages` and beyond `max_depth`.
- `config.py` defaults: `max_pages = 200`, `max_depth = 5`, `max_time_minutes = 30`.

Report evidence:

- `goboult.co.in`: found 200 URLs, crawled 19.
- `zynova-solutions.com`: found 200 URLs, crawled 199, failed 1.

Correct wording should be:

> Discovers crawlable internal pages through sitemaps, page links, markdown links, bare URLs, and limited interactive navigation, within configured page, depth, robots, and time limits.

---

### 2. "Fetches each page using a 6-stage fallback pipeline"

**Verdict:** Mostly true.

The pipeline contains six stages:

1. Crawl4AI
2. Scrapling
3. Jina Reader
4. curl_cffi TLS
5. curl_cffi Rotated
6. httpx Plain

Code evidence:

- `pipeline.py` defines the ordered stage list.
- `pipeline.py` returns the first successful stage result.
- `pipeline.py` records all attempts before the winning stage.

Limitations:

- Not every discovered URL is fetched; only queued URLs processed before limits expire are fetched.
- If one stage succeeds, later stages are not attempted.
- If Jina succeeds, it usually returns markdown/text without raw HTML, reducing downstream structured extraction.

---

### 3. "Extracts structured content: text, headings, images, videos, tables, lists"

**Verdict:** True only when usable HTML exists.

Code evidence:

- `extraction/content_extractor.py` extracts title, headings, paragraphs, lists, tables, images, videos, links, raw text, and word count.
- But if `html` is empty, `ContentExtractor.extract()` returns immediately.

Important limitation:

When `jina_reader` wins, the response often contains markdown/text but no HTML. In that case:

- `raw_text` may be filled later from pipeline text.
- headings, paragraphs, images, videos, tables, meta tags, and links are not fully extracted from the markdown.

Report evidence:

- In `goboult.co.in`, 12 of 19 pages used `jina_reader`.
- A sampled Jina page had raw text but `0` paragraphs, `0` images, `0` meta description length, and `0` internal links.

So the walkthrough overstates extraction completeness for markdown-only stages.

---

### 4. "Finds contact information: emails, phone numbers, addresses, social media links"

**Verdict:** Mostly true, with narrow extraction rules.

Code evidence:

- `extraction/contact_extractor.py` extracts:
  - emails from HTML and raw text
  - phones from `tel:` links and strict visible-text patterns
  - addresses from semantic `<address>` tags
  - social media links from regex patterns

Limitations:

- Addresses are only extracted from `<address>` tags, so plain-text addresses outside those tags are missed.
- Phone detection is intentionally strict and can miss valid phone formats.
- Social extraction stores the first matched URL per platform.

This is a reasonable implementation, but not complete "A to Z" contact discovery.

---

### 5. "Captures SEO metadata: title, description, Open Graph tags, JSON-LD"

**Verdict:** True for HTML-backed pages; false for markdown-only pages.

Code evidence:

- `extraction/meta_extractor.py` extracts:
  - `<title>`
  - meta description
  - meta keywords
  - `og:title`
  - `og:image`
  - `og:description`
  - canonical URL
  - language
  - JSON-LD structured data

Limitations:

- Metadata extraction depends on HTML.
- Jina-only pages generally do not get SEO metadata because `extract_meta(html)` receives empty HTML.
- The walkthrough's data model section omits `og_description`, which exists in `meta_extractor.py` but is not represented in `PageData`.

---

### 6. "Interactive navigation discovery for SPA sites"

**Verdict:** True, but limited.

Code evidence:

- `discovery/nav_discovery.py` uses Playwright.
- It searches for specific menu trigger selectors.
- It hovers/clicks menu items and records navigated URLs.
- `crawler/orchestrator.py` only runs this when sitemap discovery returns fewer than 5 URLs.

Limitations:

- It only explores known trigger patterns.
- It is not a full browser exploration engine.
- It does not guarantee all JavaScript routes will be found.
- It only activates when sitemap URL count is below 5.

---

### 7. "Link Discovery - 4 strategies"

**Verdict:** Mostly true, with one extra strategy in code.

Code evidence:

- `discovery/link_extractor.py` extracts:
  - `<a href>` links
  - SPA data attributes such as `data-href`, `data-url`, `data-link`, `data-route`
  - markdown links
  - bare URLs when fewer than 3 internal links are found
  - extra `<link href>` tags, excluding stylesheets/icons/preconnects

Limitations:

- Bare URL fallback only scans markdown text, not the extracted raw text.
- URL discovery depends heavily on HTML/markdown availability.
- File extensions such as `.pdf`, `.xml`, `.json`, images, media, CSS, JS, and fonts are excluded.

---

### 8. "URL queue system has deduplication, priority, depth, page budget"

**Verdict:** Mostly true.

Code evidence:

- `crawler/url_queue.py` normalizes URLs.
- `_seen` prevents duplicate queue entries.
- `asyncio.PriorityQueue` enforces priority order.
- URLs beyond `max_depth` are rejected.
- URLs beyond `max_pages` are rejected.

Important issue:

The page budget is enforced against `seen` URLs, not successfully crawled pages. This means the crawler can hit `max_pages` before crawling all accepted URLs.

This is visible in output:

- `goboult.co.in`: `total_urls_found = 200`, `total_pages_crawled = 19`.

---

### 9. "Concurrency and rate limiting"

**Verdict:** Rate limiting is true; concurrency is misleading.

Code evidence:

- `crawler/orchestrator.py` creates `asyncio.Semaphore(max_concurrent)`.
- But the crawl loop awaits each `_process_page()` call directly inside the loop.
- There is no `asyncio.create_task()` or `asyncio.gather()` fanout.

Result:

The semaphore exists, but the effective crawl behavior is serial. The walkthrough's statement that the semaphore limits parallel fetches is technically present in code, but practically misleading because no parallel page tasks are launched.

---

### 10. "Checkpoint save/resume"

**Verdict:** Save exists; resume behavior is questionable.

Code evidence:

- `crawler/checkpoint.py` writes `crawl_state.json.tmp`, then renames it.
- It stores completed pages, seen URLs, failed URLs, base URL, domain, and crawl start time.
- `crawler/orchestrator.py` saves every configured number of completed pages.
- On successful completion, the checkpoint is deleted.

Potential bug:

On resume, `crawler/orchestrator.py` re-adds every seen URL to the live queue with `depth=0` and `priority=99`. That marks them as seen, but also queues them. The comment says they "won't actually be queued", but the first add of each restored URL will queue it because the queue starts empty.

So the walkthrough claim that resume continues without re-crawling already-completed pages is not proven and is likely incorrect.

---

### 11. "Report generation in Markdown and JSON"

**Verdict:** True, with naming mismatch in current outputs.

Code evidence:

- `output/markdown_report.py` writes `report.md`.
- `output/json_export.py` writes `data.json`.
- `main.py` writes outputs under `args.output_dir / report.domain`.

Current output evidence:

- `aperions.com` contains `report1.md`, not `report.md`.
- `goboult.co.in` contains `report2.md`, not `report.md`.
- `zynova-solutions.com` contains `report.md`.

This suggests the reports may have been manually renamed or generated by a modified/local process. The code itself writes `report.md`.

---

### 12. "Markdown report is comprehensive"

**Verdict:** Partly true, but not full content.

The Markdown report includes:

- overview
- contact information
- site structure
- page details
- all pages index
- failed URLs
- external links

But page details are intentionally summarized:

- headings are limited to the first 5 per level
- content preview is limited to the first 3 paragraphs
- long paragraphs are truncated to 300 characters
- external links are limited to the first 100

Therefore, Markdown is a readable summary, not a complete extraction dump.

The JSON report is the fuller artifact.

---

## Data Completeness Assessment

### `aperions.com`

This crawl supports the walkthrough's capabilities well:

- 29 URLs found
- 29 pages crawled
- 0 failed pages
- all pages fetched via `crawl4ai`
- all pages had raw text, paragraphs, meta descriptions, and images

This is the strongest successful example.

### `goboult.co.in`

This crawl contradicts the "entire site" claim:

- 200 URLs found
- only 19 pages crawled
- crawl duration was about 30 minutes, matching the default time budget
- 12 pages used `jina_reader`, causing reduced structured extraction

This is the clearest proof that the crawler does not fetch all possible pages by default.

### `zynova-solutions.com`

This crawl shows broad coverage but still cannot prove completeness:

- 200 URLs found
- 199 pages crawled
- 1 failed page
- exact 200 URL count indicates the default page budget was reached
- pages reached depth 5, the default maximum depth

Because both page and depth limits are active, more pages may exist beyond the crawl boundary.

---

## Missing or Misleading Parts in the Walkthrough

1. The phrase "every page" should be removed or qualified.
2. The phrase "A to Z" should be removed or qualified.
3. The walkthrough should explain that Jina markdown-only pages do not receive full HTML-based extraction.
4. The walkthrough should state that Markdown output is a summary, while JSON contains fuller page data.
5. The concurrency section should be corrected because the current loop is effectively serial.
6. The resume section should be revisited because the current implementation appears to requeue restored seen URLs.
7. The output naming section should acknowledge that code writes `report.md`, while current folders contain `report1.md` and `report2.md` for two reports.
8. The system should not be described as having a database. The current persistent outputs are JSON, Markdown, and temporary checkpoint JSON.

---

## Recommended Corrected Summary

Use this instead of the current absolute claim:

> The Universal Web Crawler starts from a single URL and discovers crawlable internal pages using sitemaps, rendered-page links, markdown links, bare URL extraction, and limited interactive navigation discovery. It fetches each processed page through a six-stage fallback pipeline and extracts structured content, contact information, SEO metadata, links, and media when the successful fetch stage provides suitable HTML or text. Crawling is bounded by configured page, depth, robots, and time limits, and outputs are generated as Markdown summaries and JSON data files.

---

## Final Conclusion

The crawler is a capable universal website crawler, but it is not an exhaustive website mirror and does not guarantee complete extraction from every page. The walkthrough should be treated as a feature overview, not a proof of full-site completeness.

The actual verified claim is:

> It crawls and extracts as much discoverable site content as possible within configured limits and fetch-stage constraints.

