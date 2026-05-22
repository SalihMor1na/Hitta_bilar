"""Headless browser client using Playwright for JavaScript-rendered pages."""
import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass


async def fetch_rendered_html(
    url: str,
    wait_selector: Optional[str] = None,
    timeout_ms: int = 20000,
    delay_ms: int = 2000,
) -> Optional[str]:
    """
    Fetch a URL using headless Chromium, waiting for JavaScript to render.
    Returns fully rendered HTML or None on failure.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        logger.error(json.dumps({
            "event": "playwright_not_installed",
            "hint": "Run: pip install playwright && playwright install chromium",
        }))
        return None

    # Cookie consent button selectors — tried in order, first match wins
    _CONSENT_SELECTORS = [
        "#onetrust-accept-btn-handler",
        ".onetrust-accept-btn-handler",
        "button[id*='accept-all']",
        "button[id*='acceptAll']",
        "button[class*='accept-all']",
        "button[class*='acceptAll']",
        "[data-testid*='accept']",
        "button[title*='Acceptera alla']",
        "button[title*='Accept all']",
    ]
    _CONSENT_TEXTS = ["Acceptera alla", "Godkänn alla", "Accept all", "Acceptera", "Tillåt alla"]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(locale="sv-SE")
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            # Try to dismiss cookie consent dialog (2 second window)
            await asyncio.sleep(1.5)
            for sel in _CONSENT_SELECTORS:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(1.0)
                        logger.debug(json.dumps({"event": "cookie_consent_dismissed", "selector": sel, "url": url}))
                        break
                except Exception:
                    pass
            else:
                # Try text-based matching as fallback
                for text in _CONSENT_TEXTS:
                    try:
                        btn = page.locator(f"button:has-text('{text}')").first
                        if await btn.is_visible(timeout=500):
                            await btn.click()
                            await asyncio.sleep(1.0)
                            logger.debug(json.dumps({"event": "cookie_consent_dismissed", "text": text, "url": url}))
                            break
                    except Exception:
                        pass

            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except Exception:
                    # Selector didn't appear — still return what we have
                    pass
            else:
                # Wait for network to go idle (no requests for 500ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except Exception:
                    # Timeout is fine — take what we have
                    pass

            # Extra pause to let any remaining renders settle
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)

            html = await page.content()
            logger.debug(json.dumps({"event": "browser_fetch_ok", "url": url, "bytes": len(html)}))
            return html
        except Exception as e:
            logger.warning(json.dumps({"event": "browser_fetch_error", "url": url, "error": str(e)}))
            return None
        finally:
            await browser.close()
