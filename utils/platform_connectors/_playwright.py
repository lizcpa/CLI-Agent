from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def render_page(
    url: str,
    wait_for_selector: str | None = None,
    timeout_ms: int = 30000,
    extract_script: str | None = None,
) -> Any:
    """Lazy-load Playwright, render the page, return extracted data or HTML.

    Returns None if Playwright is not installed or rendering fails. Callers
    must handle None as a fail-soft empty result.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        logger.warning("playwright_not_installed", error=str(e))
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                if wait_for_selector:
                    await page.wait_for_selector(wait_for_selector, timeout=timeout_ms)
                if extract_script:
                    return await page.evaluate(extract_script)
                return await page.content()
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("render_page_failed", url=url, error=str(e))
        return None
