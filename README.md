# 🕷️ Universal Website Crawler

A robust, dual-mode Python crawler that can either **map and extract content from entire websites** (universal mode) or **scrape IT job listings** from company career pages (legacy mode).

It features a 6-stage fallback fetch pipeline that automatically escalates through increasingly powerful HTTP strategies — from a headless browser down to plain HTTPX — to ensure maximum page reachability even against anti-bot measures.

---

## ✨ Features

### Universal Crawl Mode (`--mode crawl`)
- 🗺️ **Full-site mapping** — discovers URLs via sitemap.xml, robots.txt, and recursive link following
- 📄 **Rich content extraction** — headings, paragraphs, lists, tables, images, and structured data (JSON-LD)
- 📬 **Contact intelligence** — aggregates emails, phone numbers, social media links across all pages
- 🔖 **SEO / meta data** — captures meta descriptions, OG tags, canonical URLs per page
- 💾 **Checkpointing** — saves crawl state every N pages; resume interrupted runs with `--resume`
- 📊 **Dual output** — generates a Markdown report and a machine-readable JSON export
- ⚙️ **Configurable limits** — max pages, depth, time budget, concurrency, and request delay

### Legacy IT Job Mode (`--mode jobs`)
- 🔍 **IT role filtering** — 3-pass classifier (prefix → rejection → deep-match) for accurate IT job detection
- 🏷️ **Rich job metadata** — title, location, department, type, salary, and direct URL
- 📤 **Flexible output** — human-readable text or structured JSON
- 🧩 **Backward compatible** — the original single-page job crawling behaviour is fully preserved

### 6-Stage Fetch Pipeline (shared by both modes)
Stages run in order; the first successful response is used:

| # | Stage | Strategy |
|---|-------|----------|
| 1 | **Crawl4AI** | Headless Chromium browser with JS rendering |
| 2 | **Scrapling** | Smart headless browser with stealth features |
| 3 | **Jina Reader** | Cloud-based reader proxy (`r.jina.ai`) |
| 4 | **curl_cffi TLS** | TLS fingerprint impersonation (Chrome 120) |
| 5 | **curl_cffi Rotated** | Rotated browser profiles with jitter/retries |
| 6 | **httpx Plain** | Standard async HTTP/2 fallback |

---

## 📁 Project Structure

```
Crawler/
├── main.py               # Entry point & CLI argument handling
├── config.py             # PipelineConfig & CrawlerConfig dataclasses
├── models.py             # Shared data models (CrawlResponse, PageData, WebsiteReport, JobListing)
├── pipeline.py           # 6-stage fallback orchestrator (legacy jobs mode)
│
├── crawler/              # Universal crawl engine
│   ├── orchestrator.py   # Async crawl loop, rate limiting, checkpointing
│   ├── url_queue.py      # Priority URL queue with visited-set deduplication
│   └── checkpoint.py     # Save / load crawl state to disk
│
├── discovery/            # URL discovery helpers
│   ├── sitemap_parser.py # Parses sitemap.xml / sitemap index files
│   ├── robots_parser.py  # Fetches and enforces robots.txt rules
│   ├── link_extractor.py # Extracts internal links from HTML
│   └── url_utils.py      # URL normalisation and domain utilities
│
├── extraction/           # Content & job data extractors
│   ├── content_extractor.py  # Universal page content extractor (BS4)
│   ├── contact_extractor.py  # Email, phone, address, social link finder
│   ├── meta_extractor.py     # Meta tags, OG tags, JSON-LD structured data
│   └── job_extractor.py      # IT job listing parser with 3-pass IT classifier
│
├── stages/               # Individual pipeline fetch stages
│   ├── stage_crawl4ai.py
│   ├── stage_scrapling.py
│   ├── stage_jina.py
│   ├── stage_curl_tls.py
│   ├── stage_curl_rotated.py
│   └── stage_httpx.py
│
└── output/               # Report generators
    ├── markdown_report.py    # Formatted Markdown site report
    └── json_export.py        # Full JSON export of WebsiteReport
```

---

## 🚀 Quick Start

### 1. Create & activate a virtual environment

```bash
python -m venv crawl
# Windows
crawl\Scripts\activate
# macOS / Linux
source crawl/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `crawl4ai` requires a one-time browser install after `pip install`:
> ```bash
> crawl4ai-setup
> ```

---

## 💻 Usage

### Universal Website Crawl (default mode)

```bash
# Basic crawl – all defaults
python main.py https://example.com

# Limit scope
python main.py https://example.com --max-pages 100 --max-depth 3

# Custom output directory and time budget
python main.py https://example.com --output ./reports --max-time 10

# Faster crawl without robots.txt compliance
python main.py https://example.com --no-robots --concurrent 10 --delay 0.5

# Resume an interrupted crawl
python main.py https://example.com --resume

# Output JSON only
python main.py https://example.com --format json

# Enable debug logging
python main.py https://example.com --verbose
```

### Legacy IT Job Crawler

```bash
# Crawl a career page for IT jobs (default output: text)
python main.py https://company.com/careers --mode jobs

# Show all jobs, not just IT roles
python main.py https://company.com/careers --mode jobs --all-jobs

# Output as JSON
python main.py https://company.com/careers --mode jobs --jobs-output json

# Use a Jina API key for higher rate limits
python main.py https://company.com/careers --mode jobs --jina-key YOUR_KEY

# Disable browser-based stages (faster, less capable)
python main.py https://company.com/careers --mode jobs --disable-browser
```

---

## ⚙️ CLI Reference

### Global Options

| Flag | Default | Description |
|------|---------|-------------|
| `url` | *(required)* | URL to crawl |
| `--mode` | `crawl` | `crawl` (universal) or `jobs` (IT job crawler) |
| `--timeout` | `30` | Per-page fetch timeout in seconds |
| `--verbose`, `-v` | off | Enable DEBUG-level logging |

### Universal Crawl Options

| Flag | Default | Description |
|------|---------|-------------|
| `--max-pages` | `200` | Maximum successfully crawled pages |
| `--max-discovered-urls` | `1000` | Maximum unique URLs to discover/enqueue |
| `--max-depth` | `5` | Maximum link-follow depth from start URL |
| `--max-time` | `30` | Time budget in minutes |
| `--delay` | `1.5` | Seconds between requests |
| `--concurrent` | `3` | Max concurrent fetch workers |
| `--no-robots` | off | Ignore `robots.txt` restrictions |
| `--resume` | off | Resume from `crawl_state.json` checkpoint |
| `--output` | `./output` | Output directory for reports |
| `--format` | `markdown json` | Space-separated list of output formats |

### Legacy Job Crawler Options

| Flag | Default | Description |
|------|---------|-------------|
| `--disable-browser` | off | Skip Crawl4AI and Scrapling stages |
| `--disable-jina` | off | Skip Jina Reader stage |
| `--jina-key` | `""` | Jina API key for higher rate limits |
| `--all-jobs` | off | Return all jobs, not just IT roles |
| `--jobs-output` | `text` | `text` or `json` |

---

## 📤 Output

### Universal Crawl
For a crawled domain `example.com`, outputs are written to `./output/example.com/`:

```
output/
└── example.com/
    ├── report.md    # Human-readable Markdown report
    └── data.json    # Machine-readable full data export
```

The **Markdown report** includes:
- Crawl summary (pages, words, images, duration)
- Per-page content (headings, key paragraphs, links)
- Aggregated contacts (emails, phones, social profiles)
- External links

The **JSON export** is the serialised `WebsiteReport` dataclass — suitable for ingestion into a database or downstream processing.

### IT Job Mode
Results are printed to stdout — either formatted text or JSON.

---

## 🔧 Configuration

Key defaults are defined as dataclasses in `config.py`:

```python
# Universal crawler
CrawlerConfig(
    max_pages=200,              # successfully crawled page cap
    max_discovered_urls=1000,   # discovery/queue growth cap
    max_depth=5,
    max_time_minutes=30,
    request_delay=1.5,
    max_concurrent=3,
    respect_robots=True,
    checkpoint_every=25,
    checkpoint_file="crawl_state.json",
)

# Per-page fetch pipeline
PipelineConfig(
    timeout=30,
    enable_crawl4ai=True,
    enable_scrapling=True,
    enable_jina=True,
    curl_impersonate="chrome120",
)
```

All values can be overridden at runtime via CLI flags.

---

## 📦 Dependencies

| Package | Purpose |
|---------|---------|
| `crawl4ai` | Headless Chromium browser via Playwright |
| `scrapling` | Stealth headless browser |
| `curl-cffi` | TLS fingerprint impersonation |
| `httpx[http2]` | Async HTTP/2 client |
| `beautifulsoup4` + `lxml` | HTML parsing |
| `pydantic` | Data validation |
| `jinja2` | Report templating |
| `rich` | Terminal output formatting |
| `tldextract` | Registrable domain extraction |
| `openai` | (Optional) AI-assisted extraction |
| `dotenv` | `.env` file support |

---

## 📝 License

This project is for personal / internal use. No license is currently specified.
