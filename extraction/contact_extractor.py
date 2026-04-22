"""
Contact info extractor — find emails, phone numbers, addresses, and social links.

Uses regex patterns and HTML tag analysis to extract contact information
from page text and HTML.
"""

import re
import logging
from urllib.parse import urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# ── Regex Patterns ───────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Common phone patterns (international + US/UK/EU formats)
_PHONE_RE = re.compile(
    r"""
    (?:
        \+?\d{1,3}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{2,4}[\s\-.]?\d{2,4}[\s\-.]?\d{0,4}  # intl
        | \(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}                                          # US
        | \d{4,5}[\s.\-]?\d{6,7}                                                          # UK
    )
    """,
    re.VERBOSE,
)

# Social media URL patterns
_SOCIAL_PATTERNS: dict[str, re.Pattern] = {
    "twitter":   re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(\w+)", re.I),
    "linkedin":  re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[\w\-]+", re.I),
    "facebook":  re.compile(r"https?://(?:www\.)?facebook\.com/[\w.\-]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[\w.\-]+", re.I),
    "youtube":   re.compile(r"https?://(?:www\.)?youtube\.com/(?:c/|channel/|@)[\w\-]+", re.I),
    "github":    re.compile(r"https?://(?:www\.)?github\.com/[\w\-]+", re.I),
    "tiktok":    re.compile(r"https?://(?:www\.)?tiktok\.com/@[\w.\-]+", re.I),
}

# Emails to exclude (common false positives)
_JUNK_EMAIL_DOMAINS = frozenset({
    "example.com", "example.org", "test.com",
    "sentry.io", "wixpress.com", "w3.org",
    "schema.org", "googleapis.com", "gravatar.com",
})


def extract_contact_info(
    html: str,
    raw_text: str = "",
) -> dict:
    """
    Extract all contact information from a page.

    Returns:
        {
            "emails": ["contact@example.com", ...],
            "phones": ["+1-800-000-0000", ...],
            "addresses": ["123 Main St, ...", ...],
            "social_links": {"twitter": "https://...", "linkedin": "https://...", ...},
        }
    """
    result = {
        "emails": [],
        "phones": [],
        "addresses": [],
        "social_links": {},
    }

    # ── Emails ──
    combined_text = f"{raw_text}\n{html}" if raw_text else html
    emails = set()
    for match in _EMAIL_RE.finditer(combined_text):
        email = match.group(0).lower().rstrip(".")
        domain = email.split("@")[1] if "@" in email else ""
        if domain not in _JUNK_EMAIL_DOMAINS:
            emails.add(email)
    result["emails"] = sorted(emails)

    # ── Phones — from tel: links + text patterns ──
    phones: set[str] = set()
    try:
        soup = BeautifulSoup(html, "lxml")

        # tel: links are the most reliable source
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("tel:"):
                phone = href[4:].strip()
                if phone and len(phone) >= 7:
                    phones.add(phone)
    except Exception:
        pass

    # Text-based phone extraction (more noisy — keep only if reasonable length)
    text_for_phones = raw_text or ""
    for match in _PHONE_RE.finditer(text_for_phones):
        phone = match.group(0).strip()
        digits_only = re.sub(r"\D", "", phone)
        if 7 <= len(digits_only) <= 15:
            phones.add(phone)

    result["phones"] = sorted(phones)

    # ── Addresses — from <address> tag ──
    addresses: list[str] = []
    try:
        soup = soup if "soup" in dir() else BeautifulSoup(html, "lxml")
        for addr_tag in soup.find_all("address"):
            addr_text = addr_tag.get_text(separator=", ", strip=True)
            if addr_text and len(addr_text) > 10:
                addresses.append(addr_text)
    except Exception:
        pass
    result["addresses"] = addresses

    # ── Social Links ──
    social: dict[str, str] = {}
    for platform, pattern in _SOCIAL_PATTERNS.items():
        match = pattern.search(html)
        if match:
            social[platform] = match.group(0)
    result["social_links"] = social

    return result
