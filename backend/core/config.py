"""Configuration for the crawling pipeline."""

from dataclasses import dataclass, field

from backend.paths import CHECKPOINT_FILE, OUTPUT_DIR


@dataclass
class PipelineConfig:
    """Master configuration for all pipeline stages."""

    # --- Global ---
    timeout: int = 30                # seconds per stage
    min_content_length: int = 200    # minimum chars to consider content valid
    # Runtime-only cooperative cancellation signal; never persisted or exposed.
    cancellation_event: object | None = None

    # --- Stage Toggles ---
    enable_crawl4ai: bool = True
    enable_scrapling: bool = True
    enable_jina: bool = True
    enable_curl_tls: bool = True
    enable_curl_rotated: bool = True
    enable_httpx: bool = True

    # --- Crawl4AI Config ---
    crawl4ai_headless: bool = True
    crawl4ai_wait_for: str = ""      # CSS selector to wait for (e.g., ".job-listing")
    crawl4ai_js_code: str = ""       # JS to execute before extraction
    crawl4ai_wait_seconds: float = 3.0

    # --- Scrapling Config ---
    scrapling_headless: bool = True
    scrapling_wait_seconds: float = 3.0

    # --- Jina Config ---
    jina_api_key: str = ""           # Optional, for higher rate limits
    jina_base_url: str = "https://r.jina.ai/"

    # --- curl_cffi Config ---
    curl_impersonate: str = "chrome120"
    curl_rotated_max_retries: int = 4
    curl_rotated_base_delay: float = 2.0
    curl_rotated_max_jitter: float = 1.5
    curl_rotated_profiles: list[str] = field(default_factory=lambda: [
        "chrome120",
        "firefox120",
        "safari17_2_ios",
        "edge120",
    ])

    # --- Headers ---
    default_headers: dict = field(default_factory=lambda: {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    })

    # --- IT Job Keywords ---
    it_keywords: list[str] = field(default_factory=lambda: [
        # Roles
        "software engineer", "software developer", "web developer",
        "frontend", "front-end", "backend", "back-end", "full stack",
        "fullstack", "devops", "sre", "site reliability",
        "data engineer", "data scientist", "data analyst",
        "machine learning", "ml engineer", "ai engineer",
        "cloud engineer", "cloud architect", "solutions architect",
        "systems engineer", "network engineer", "security engineer",
        "cybersecurity", "information security", "infosec",
        "database administrator", "dba", "platform engineer",
        "qa engineer", "quality assurance", "test engineer", "sdet",
        "mobile developer", "ios developer", "android developer",
        "product manager", "technical program manager",
        "scrum master", "agile coach",
        "ui/ux", "ux designer", "ui designer",
        "technical writer", "developer advocate",
        "it support", "it administrator", "system administrator",
        "help desk", "desktop support",
        "business analyst", "business intelligence",
        "erp", "sap", "salesforce",
        # Technologies (appearing in titles)
        "python", "java", "javascript", "typescript", "react",
        "angular", "vue", "node.js", "golang", "rust", "c++",
        "kubernetes", "docker", "aws", "azure", "gcp",
        "terraform", "jenkins", "ci/cd",
        "sql", "nosql", "mongodb", "postgresql",
        "linux", "unix",
    ])

    it_department_keywords: list[str] = field(default_factory=lambda: [
        "engineering", "technology", "it", "information technology",
        "r&d", "research and development", "product", "platform",
        "infrastructure", "data", "analytics", "security",
        "development", "technical", "digital",
    ])


# ═══════════════════════════════════════════════════════════════════════
# Universal Crawler config (new — does NOT affect legacy PipelineConfig)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class CrawlerConfig:
    """Configuration for the universal website crawler."""

    # Scope limits
    max_pages: int = 200                # hard cap on successfully crawled pages
    max_discovered_urls: int = 1000     # hard cap on unique URLs to discover/enqueue
    max_depth: int = 5                  # max link-follow depth from homepage
    max_time_minutes: int = 30          # time budget for entire crawl

    # Rate limiting
    request_delay: float = 1.5          # seconds between requests
    max_concurrent: int = 3             # concurrent fetches
    cancellation_grace_seconds: int = 30

    # Crawl behaviour
    respect_robots: bool = True         # obey robots.txt

    # Checkpointing
    checkpoint_every: int = 25          # save state every N pages
    checkpoint_file: str = str(CHECKPOINT_FILE)

    # Output
    output_dir: str = str(OUTPUT_DIR)   # where to write reports
    output_formats: list[str] = field(default_factory=lambda: ["markdown", "json"])

    # Pipeline config (reused for per-page fetching)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

