import asyncio
import logging

import httpx
from scrapy import signals
from scrapy.exceptions import DropItem

from db.config import settings
from mediafusion_scrapy.scrapling_adapter import download_torrent_with_challenge
from utils import torrent

logger = logging.getLogger(__name__)


class PlaywrightTorrentDownloadPipeline:
    """Download .torrent files using Scrapling Playwright fetcher."""

    MAX_RETRIES = 2
    FETCH_TIMEOUT_MS = 60_000
    MAX_PARALLEL_DOWNLOADS = 2

    def __init__(self):
        self._http_client = None
        self._download_semaphore = asyncio.Semaphore(self.MAX_PARALLEL_DOWNLOADS)

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        crawler.signals.connect(pipeline._open, signal=signals.spider_opened)
        crawler.signals.connect(pipeline._close, signal=signals.spider_closed)
        return pipeline

    async def _open(self):
        proxy_url = settings.scrapling_proxy_url or settings.requests_proxy_url
        client_kwargs = {"follow_redirects": True}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        self._http_client = httpx.AsyncClient(**client_kwargs)
        logger.info("Scrapling torrent download pipeline initialized")

    async def _close(self):
        if self._http_client:
            await self._http_client.aclose()
        self._http_client = None
        logger.info("Scrapling torrent download pipeline closed")

    async def _fetch_torrent(self, torrent_url: str, referer_url: str | None = None) -> bytes | None:
        proxy_url = settings.scrapling_proxy_url or settings.requests_proxy_url
        try:
            return await download_torrent_with_challenge(
                torrent_url,
                # Keep sports torrent fetches strictly headless to avoid visible
                # browser popups during scheduled/background spider runs.
                headless=settings.scrapling_headless,
                disable_resources=settings.scrapling_disable_resources,
                network_idle=settings.scrapling_network_idle,
                wait_time_ms=settings.scrapling_wait_time_ms,
                timeout_ms=settings.scrapling_timeout_ms,
                google_search_referer=settings.scrapling_google_search_referer,
                proxy_url=proxy_url,
                client=self._http_client,
                referer_url=referer_url,
                fetcher_mode=settings.scrapling_fetcher_mode,
                solve_cloudflare=settings.scrapling_solve_cloudflare,
                real_chrome=settings.scrapling_real_chrome,
            )
        except Exception:
            logger.warning("Scrapling failed to download torrent from %s", torrent_url, exc_info=True)
            return None

    async def process_item(self, item):
        torrent_link = item.get("torrent_link")
        if not torrent_link:
            raise DropItem("No torrent link found in item.")

        if not self._http_client:
            raise DropItem("Scrapling HTTP client not available")

        torrent_bytes = None
        referer_url = item.get("webpage_url")
        async with self._download_semaphore:
            for attempt in range(1, self.MAX_RETRIES + 1):
                torrent_bytes = await self._fetch_torrent(torrent_link, referer_url=referer_url)
                if torrent_bytes and torrent_bytes[0:1] == b"d":
                    break

                logger.warning(
                    "Download failed (attempt %d/%d) for %s, re-solving challenge",
                    attempt,
                    self.MAX_RETRIES,
                    torrent_link,
                )
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(2 * attempt)

        if not torrent_bytes:
            raise DropItem(f"Failed to download torrent after retries: {torrent_link}")

        torrent_metadata = torrent.extract_torrent_metadata(torrent_bytes, item.get("parsed_data"))
        if not torrent_metadata:
            raise DropItem(f"Failed to parse torrent metadata: {torrent_link}")

        item.update(torrent_metadata)
        return item
