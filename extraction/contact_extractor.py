"""
Contact info extractor — find emails, phone numbers, addresses, and social links.

Uses regex patterns and HTML tag analysis to extract contact information
from page text and HTML.

PHONE NUMBER STRATEGY:
- Primary: Extract from <a href="tel:..."> links (most reliable, zero false positives)
- Secondary: Regex on visible text ONLY, requiring either:
  - A leading "+" (international format)
  - Or appearing near phone-related keywords ("phone", "call", "tel", etc.)
- Numbers from CSS, image paths, dates, etc. are NOT matched.
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

# Phone regex — STRICT: requires "+" prefix for international numbers
# or standard parenthesized area codes like (123) 456-7890
_PHONE_STRICT_RE = re.compile(
    r"""
    (?:
        \+\d{1,3}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{2,4}[\s\-.]?\d{2,5}   # +91 8128780878
        | \(\d{3}\)[\s.\-]?\d{3}[\s.\-]?\d{4}                            # (123) 456-7890
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

    soup = None
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            pass

    # ── Emails — from HTML + text ──
    combined_text = f"{raw_text}\n{html}" if raw_text else html
    emails = set()
    for match in _EMAIL_RE.finditer(combined_text):
        email = match.group(0).lower().rstrip(".")
        domain = email.split("@")[1] if "@" in email else ""
        if domain not in _JUNK_EMAIL_DOMAINS:
            emails.add(email)
    result["emails"] = sorted(emails)

    # ── Phones — STRICT extraction ──
    phones: set[str] = set()

    # Strategy 1: tel: links (most reliable — site explicitly marks these as phone numbers)
    if soup:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("tel:"):
                phone = href[4:].strip()
                # Clean up common tel: link formatting
                phone = phone.replace("%20", " ").replace("%2B", "+")
                digits_only = re.sub(r"\D", "", phone)
                if 7 <= len(digits_only) <= 15:
                    phones.add(phone)

    # Strategy 2: Strict regex on visible text only (not HTML source)
    # Use raw_text (which is cleaned of HTML tags) to avoid matching CSS/JS numbers
    visible_text = raw_text or ""
    if visible_text:
        for match in _PHONE_STRICT_RE.finditer(visible_text):
            phone = match.group(0).strip()
            digits_only = re.sub(r"\D", "", phone)
            if 7 <= len(digits_only) <= 15:
                phones.add(phone)

    result["phones"] = sorted(phones)

    # ── Addresses — from <address> tag ──
    addresses: list[str] = []
    if soup:
        for addr_tag in soup.find_all("address"):
            addr_text = addr_tag.get_text(separator=", ", strip=True)
            if addr_text and len(addr_text) > 10:
                addresses.append(addr_text)
    result["addresses"] = addresses

    # ── Social Links ──
    social: dict[str, str] = {}
    for platform, pattern in _SOCIAL_PATTERNS.items():
        match = pattern.search(html)
        if match:
            social[platform] = match.group(0)
    result["social_links"] = social

    return result
