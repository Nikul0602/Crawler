"""
Interactive navigation discovery — discover URLs from JavaScript-driven menus.

For SPA sites (Next.js, React, Angular) where navigation uses <button> elements
and JavaScript routing instead of standard <a href> links, this module uses
Playwright to:

1. Load the homepage in a real browser
2. Find interactive nav elements (buttons with role="menuitem", nav items)
3. Hover each one with real pointer events to trigger dropdowns
4. Click dropdown items and capture the URL the browser navigates to

Uses Playwright directly because React/Radix UI requires real pointer events.
Dispatching DOM events via JavaScript does NOT trigger React synthetic handlers.

This runs ONLY when sitemap discovery yields few/no URLs, so it adds zero
overhead for standard HTML sites like WordPress.
"""

import asyncio
import logging
import re
from urllib.parse import urljoin

from discovery.url_utils import normalize_url, is_same_domain, extract_base_domain, should_exclude

logger = logging.getLogger(__name__)

# Selectors for SPA menu trigger buttons
_TRIGGER_SELECTORS = [
    'button[role="menuitem"]',
    '[data-slot="menubar-trigger"]',
    'nav button[aria-haspopup="menu"]',
    'header button[aria-haspopup="menu"]',
    '[role="navigation"] button[aria-haspopup]',
]

# Selectors for dropdown items inside opened menus
_DROPDOWN_ITEM_SELECTORS = [
    '[data-state="open"] [role="menuitem"]:not([aria-haspopup="menu"])',
    '[data-radix-popper-content-wrapper] [role="menuitem"]:not([aria-haspopup="menu"])',
    '[role="menu"] [role="menuitem"]:not([aria-haspopup="menu"])',
    '[data-state="open"] a[href]',
    '[data-radix-popper-content-wrapper] a[href]',
]


async def discover_nav_links(
    url: str,
    timeout: int = 30,
) -> list[str]:
    """
    Use Playwright to interact with navigation menus and discover URLs.

    Args:
        url: The homepage URL to explore.
        timeout: Page load timeout in seconds.

    Returns:
        List of discovered internal URLs (normalized, deduplicated).
    """
    base_domain = extract_base_domain(url)
    discovered: set[str] = set()

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            try:
                # Load homepage
                await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                await page.wait_for_timeout(1000)

                # Collect any standard links already on the page
                await _collect_page_links(page, url, base_domain, discovered)

                # Find trigger selector that matches
                trigger_selector = None
                trigger_count = 0
                for sel in _TRIGGER_SELECTORS:
                    count = await page.locator(sel).count()
                    if count > 0:
                        trigger_selector = sel
                        trigger_count = count
                        logger.info(
                            f"[nav_discovery] Found {count} triggers via '{sel}'"
                        )
                        break

                if not trigger_selector:
                    logger.info("[nav_discovery] No interactive nav triggers found")
                    return _finalize(discovered, url)

                # --- Explore each trigger ---
                # We iterate by index, re-querying each time since navigation
                # destroys element handles.
                for i in range(trigger_count):
                    try:
                        await _explore_trigger(
                            page, url, base_domain, discovered,
                            trigger_selector, i, timeout,
                        )
                    except Exception as e:
                        logger.debug(f"[nav_discovery] Trigger {i} error: {e}")
                        # Ensure we're back on homepage for the next trigger
                        try:
                            await page.goto(
                                url, wait_until="networkidle",
                                timeout=timeout * 1000,
                            )
                            await page.wait_for_timeout(500)
                        except Exception:
                            pass

            finally:
                await context.close()
                await browser.close()

        return _finalize(discovered, url)

    except ImportError:
        logger.warning("[nav_discovery] playwright not installed, skipping")
        return []
    except Exception as e:
        logger.error(f"[nav_discovery] Error: {e}")
        return []


async def _explore_trigger(
    page,
    homepage_url: str,
    base_domain: str,
    discovered: set[str],
    trigger_selector: str,
    trigger_index: int,
    timeout: int,
) -> None:
    """
    Explore a single nav trigger: hover to open dropdown, then click each item.

    After each item click causes navigation, we go back to homepage and
    re-query all elements (since navigation destroys element handles).
    """
    # Re-query the trigger by index (handles are fresh each time)
    triggers = page.locator(trigger_selector)
    trigger = triggers.nth(trigger_index)

    trigger_text = (await trigger.text_content() or "").strip()
    logger.debug(f"[nav_discovery] Exploring trigger {trigger_index+1}: '{trigger_text}'")

    # Hover to open dropdown
    await trigger.hover()
    await page.wait_for_timeout(500)

    # Click if hover didn't open it
    state = await trigger.get_attribute("data-state")
    if state != "open":
        await trigger.click()
        await page.wait_for_timeout(500)

    # Collect any <a> links that appeared
    await _collect_page_links(page, homepage_url, base_domain, discovered)

    # Find dropdown items
    dropdown_items_info = await _get_dropdown_items_text(page, trigger_text)

    if not dropdown_items_info:
        logger.debug(f"[nav_discovery] No dropdown items for '{trigger_text}'")
        await page.keyboard.press("Escape")
        return

    logger.debug(
        f"[nav_discovery] '{trigger_text}' has {len(dropdown_items_info)} items: "
        + ", ".join(dropdown_items_info)
    )

    # Click each dropdown item by text
    for item_text in dropdown_items_info:
        try:
            # Re-open the trigger (we may have navigated away)
            if page.url != homepage_url:
                await page.goto(
                    homepage_url, wait_until="networkidle",
                    timeout=timeout * 1000,
                )
                await page.wait_for_timeout(500)

            # Re-hover the trigger
            trigger = page.locator(trigger_selector).nth(trigger_index)
            await trigger.hover()
            await page.wait_for_timeout(500)

            state = await trigger.get_attribute("data-state")
            if state != "open":
                await trigger.click()
                await page.wait_for_timeout(500)

            # Find and click the dropdown item by exact text
            item = await _find_dropdown_item_by_text(page, item_text)
            if not item:
                continue

            url_before = page.url
            await item.click()
            await page.wait_for_timeout(1500)

            url_after = page.url

            if url_after != url_before:
                normalized = normalize_url(url_after)
                if (normalized and
                    is_same_domain(normalized, base_domain) and
                    not should_exclude(normalized)):
                    discovered.add(normalized)
                    logger.info(
                        f"[nav_discovery] {trigger_text} > "
                        f"{item_text} -> {normalized}"
                    )

                # Also collect links from the page we navigated to
                await _collect_page_links(page, url_after, base_domain, discovered)

        except Exception as e:
            logger.debug(f"[nav_discovery] Item '{item_text}' error: {e}")
            continue


async def _get_dropdown_items_text(page, trigger_text: str) -> list[str]:
    """Get text content of all dropdown items currently visible."""
    items_text = []

    for sel in _DROPDOWN_ITEM_SELECTORS:
        elements = await page.query_selector_all(sel)
        if not elements:
            continue

        for el in elements:
            text = (await el.text_content() or "").strip()
            if text and text != trigger_text and len(text) < 100:
                items_text.append(text)

        if items_text:
            break

    return items_text


async def _find_dropdown_item_by_text(page, text: str):
    """Find a dropdown item element by its text content."""
    for sel in _DROPDOWN_ITEM_SELECTORS:
        elements = await page.query_selector_all(sel)
        for el in elements:
            el_text = (await el.text_content() or "").strip()
            if el_text == text:
                return el
    return None


async def _collect_page_links(
    page, page_url: str, base_domain: str, discovered: set[str],
) -> None:
    """Collect all internal <a href> links currently visible on the page."""
    try:
        links = await page.query_selector_all("a[href]")
        for link in links:
            href = await link.get_attribute("href")
            if not href or should_exclude(href):
                continue
            absolute = urljoin(page_url, href)
            normalized = normalize_url(absolute)
            if normalized and is_same_domain(normalized, base_domain):
                if not re.search(
                    r'\.(svg|png|jpg|jpeg|gif|css|js|ico|woff|ttf)$',
                    normalized, re.I,
                ):
                    discovered.add(normalized)
    except Exception:
        pass


def _finalize(discovered: set[str], homepage_url: str) -> list[str]:
    """Remove homepage and return sorted list."""
    homepage_normalized = normalize_url(homepage_url)
    discovered.discard(homepage_normalized)
    result = sorted(discovered)
    logger.info(
        f"[nav_discovery] Total discovered: {len(result)} URLs "
        f"via interactive navigation"
    )
    return result
