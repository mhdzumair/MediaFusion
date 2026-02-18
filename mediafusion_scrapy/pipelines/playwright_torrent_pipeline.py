import asyncio
import base64
import logging

from playwright._impl._errors import Error as PlaywrightError
from playwright._impl._errors import TargetClosedError
from scrapy import signals
from scrapy.exceptions import DropItem

from db.config import settings
from utils import torrent

logger = logging.getLogger(__name__)

# JS snippet executed inside the browser to fetch a URL and return its bytes as base64.
_FETCH_JS = """
async (url) => {
    const r = await fetch(url, { credentials: "include", redirect: "follow" });
    if (r.status !== 200) {
        return { status: r.status, ok: false };
    }
    const buf = await r.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return { status: r.status, ok: true, size: bytes.length, data: btoa(bin) };
}
"""


class PlaywrightTorrentDownloadPipeline:
    """Download .torrent files from sites protected by JS math challenges.

    Uses a Playwright browser (via browserless CDP) to solve the initial
    challenge, then reuses the authenticated session for bulk downloads.
    If a download fails with 429, the challenge is re-solved automatically.
    """

    MAX_RETRIES = 2
    CHALLENGE_TIMEOUT_MS = 25_000
    FETCH_TIMEOUT_MS = 30_000

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()
        self._challenge_solved = False

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        crawler.signals.connect(pipeline._open, signal=signals.spider_opened)
        crawler.signals.connect(pipeline._close, signal=signals.spider_closed)
        return pipeline

    async def _open(self):
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        cdp_url = settings.playwright_cdp_url
        logger.info("Connecting to browserless at %s", cdp_url)
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url, timeout=120_000)
        ctx = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        self._page = await ctx.new_page()
        logger.info("Playwright browser session established")

    async def _close(self):
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._page = None
        self._playwright = None
        logger.info("Playwright browser session closed")

    async def _reinit_page(self):
        """Reinitialize the browser page after it has been closed unexpectedly."""
        try:
            if self._browser and self._browser.is_connected():
                ctx = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
                self._page = await ctx.new_page()
                logger.info("Reinitialized Playwright page after unexpected close")
            else:
                logger.warning("Browser disconnected; skipping page reinitialization")
                self._page = None
        except Exception:
            logger.warning("Failed to reinitialize Playwright page", exc_info=True)
            self._page = None

    async def _solve_challenge(self, torrent_url: str) -> bool:
        """Navigate to a .torrent URL so the stealth browser solves the JS challenge."""
        async with self._lock:
            if self._challenge_solved:
                return True
            try:
                logger.info("Solving JS challenge via %s", torrent_url)
                await self._page.goto(
                    torrent_url,
                    wait_until="load",
                    timeout=self.CHALLENGE_TIMEOUT_MS,
                )
                await self._page.wait_for_load_state("networkidle", timeout=self.CHALLENGE_TIMEOUT_MS)
                cookies = await self._page.context.cookies()
                if any(c["name"] == "access_challenge_global" for c in cookies):
                    self._challenge_solved = True
                    logger.info("JS challenge solved successfully")
                    return True

                logger.warning("Challenge cookie not set after navigation")
                return False
            except TargetClosedError:
                logger.warning("Browser page was closed; reinitializing for next attempt")
                self._challenge_solved = False
                await self._reinit_page()
                return False
            except PlaywrightError as e:
                if "Download is starting" in str(e):
                    # The .torrent URL triggered a direct download — the browser navigated
                    # successfully, so the JS challenge is already solved. Check cookies.
                    try:
                        cookies = await self._page.context.cookies()
                        if any(c["name"] == "access_challenge_global" for c in cookies):
                            self._challenge_solved = True
                            logger.info("JS challenge solved (download triggered)")
                            return True
                    except Exception:
                        pass
                    logger.warning("Download triggered but challenge cookie not set for %s", torrent_url)
                    return False
                logger.warning("Playwright error solving JS challenge: %s", e)
                return False
            except Exception:
                logger.warning("Failed to solve JS challenge", exc_info=True)
                return False

    async def _invalidate_challenge(self):
        """Mark the current challenge cookies as stale so the next download re-solves."""
        async with self._lock:
            self._challenge_solved = False

    async def _fetch_torrent(self, torrent_url: str) -> bytes | None:
        """Fetch a .torrent file using the browser's authenticated session."""
        result = await self._page.evaluate(_FETCH_JS, torrent_url)
        if not result.get("ok"):
            return None
        raw = base64.b64decode(result["data"])
        if not raw or raw[0:1] != b"d":
            logger.error(
                "Fetched data is not a valid torrent file for %s (%d bytes)",
                torrent_url,
                len(raw),
            )
            return None
        return raw

    async def process_item(self, item):
        torrent_link = item.get("torrent_link")
        if not torrent_link:
            raise DropItem(f"No torrent link found in item: {item}")

        if not self._page:
            raise DropItem("Playwright browser session not available")

        torrent_bytes = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            if not self._challenge_solved:
                solved = await self._solve_challenge(torrent_link)
                if not solved:
                    logger.error(
                        "Cannot solve challenge (attempt %d/%d) for %s",
                        attempt,
                        self.MAX_RETRIES,
                        torrent_link,
                    )
                    continue

            torrent_bytes = await self._fetch_torrent(torrent_link)
            if torrent_bytes:
                break

            logger.warning(
                "Download failed (attempt %d/%d) for %s, re-solving challenge",
                attempt,
                self.MAX_RETRIES,
                torrent_link,
            )
            await self._invalidate_challenge()

        if not torrent_bytes:
            raise DropItem(f"Failed to download torrent after retries: {torrent_link}")

        torrent_metadata = torrent.extract_torrent_metadata(torrent_bytes, item.get("parsed_data"))
        if not torrent_metadata:
            raise DropItem(f"Failed to parse torrent metadata: {torrent_link}")

        item.update(torrent_metadata)
        return item
