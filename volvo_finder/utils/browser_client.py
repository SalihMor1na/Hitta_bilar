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

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(locale="sv-SE")
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

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
