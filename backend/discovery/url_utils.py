"""
URL normalization, filtering, and scope enforcement utilities.
"""

import re
from urllib.parse import urlparse, urlunparse, urljoin, parse_qs, urlencode

import tldextract

_TLD_EXTRACTOR = tldextract.TLDExtract(
    suffix_list_urls=(),
    cache_dir=None,
)


# ── Extensions & prefixes to always exclude ──────────────────────────

_EXCLUDED_EXTENSIONS = frozenset({
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
    # Documents / archives
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    # Media
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".wav", ".ogg", ".webm",
    # Code / assets
    ".css", ".js", ".map", ".woff", ".woff2", ".ttf", ".eot",
    # Data
    ".xml", ".rss", ".atom", ".json", ".csv",
})

_EXCLUDED_SCHEMES = frozenset({"mailto", "tel", "javascript", "data", "ftp"})

# Query params that produce duplicate pages (tracking / sorting)
_NOISE_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid", "mc_cid", "mc_eid",
    "sort", "order", "filter", "view",
})


# ── Public API ───────────────────────────────────────────────────────

def normalize_url(url: str, base_url: str = "") -> str:
    """
    Normalize a URL for consistent deduplication.

    - Resolve relative URLs against base_url
    - Force lowercase scheme + netloc
    - Strip fragments (#section)
    - Strip tracking / noise query parameters
    - Remove trailing slash (except root "/")
    """
    if not url or not url.strip():
        return ""

    url = url.strip()

    # Resolve relative URLs
    if base_url and not url.startswith(("http://", "https://", "//")):
        url = urljoin(base_url, url)

    # Handle protocol-relative URLs
    if url.startswith("//"):
        url = "https:" + url

    # Add scheme if missing
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)

    # Skip non-HTTP schemes
    if parsed.scheme not in ("http", "https"):
        return ""

    # Lowercase scheme + host
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower().rstrip(".")

    # Strip default ports
    if netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif netloc.endswith(":443"):
        netloc = netloc[:-4]

    # Strip noise query params
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        clean_params = {
            k: v for k, v in params.items()
            if k.lower() not in _NOISE_PARAMS
        }
        query = urlencode(clean_params, doseq=True) if clean_params else ""
    else:
        query = ""

    # Normalize path — remove trailing slash (except root)
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        path = "/"

    # Reassemble WITHOUT fragment
    normalized = urlunparse((scheme, netloc, path, parsed.params, query, ""))
    return normalized


def extract_base_domain(url: str) -> str:
    """
    Extract the registrable domain from a URL using tldextract.

    Uses public suffix list for accurate domain extraction.
    Falls back to host-based parsing for localhost, IPs, and
    internal hostnames where tldextract returns no suffix.

    "https://blog.example.co.uk/path" → "example.co.uk"
    "https://www.example.com/path"    → "example.com"
    "http://localhost:8000/path"      → "localhost"
    "http://127.0.0.1:8000/path"     → "127.0.0.1"
    """
    parsed_url = url if "://" in url else f"https://{url}"
    ext = _TLD_EXTRACTOR(parsed_url)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()

    # Fallback for localhost, IP addresses, and internal hosts.
    parsed = urlparse(parsed_url)
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def is_same_domain(url: str, base_domain: str) -> bool:
    """
    Check if url belongs to the same domain (or a subdomain of it).

    is_same_domain("https://blog.example.com/x", "example.com") → True
    is_same_domain("https://other.com/x", "example.com")        → False
    """
    url_domain = extract_base_domain(url)
    base = base_domain.lower().lstrip("www.")

    # Exact match or subdomain
    return url_domain == base or url_domain.endswith(f".{base}")


def should_exclude(url: str) -> bool:
    """
    Return True if the URL should NOT be crawled.

    Filters out: non-HTTP schemes, binary files, anchors-only, etc.
    """
    if not url or not url.strip():
        return True

    url_stripped = url.strip().lower()

    # Non-HTTP schemes
    for scheme in _EXCLUDED_SCHEMES:
        if url_stripped.startswith(f"{scheme}:"):
            return True

    # Fragment-only links
    if url_stripped.startswith("#"):
        return True

    # Check file extensions
    parsed = urlparse(url_stripped)
    path = parsed.path
    if "." in path.split("/")[-1]:
        ext = "." + path.rsplit(".", 1)[-1]
        if ext in _EXCLUDED_EXTENSIONS:
            return True

    return False


def classify_url_priority(url: str) -> int:
    """
    Assign a priority score (lower = higher priority).

    0 — Homepage / root
    1 — Top-level pages (about, contact, pricing, etc.)
    2 — Second-level pages
    3 — Deep pages
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")

    if not path:
        return 0

    depth = len([p for p in path.split("/") if p])

    if depth == 1:
        return 1
    elif depth == 2:
        return 2
    else:
        return 3

