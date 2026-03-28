"""CinemagoerNG HTTP: curl_cffi first, then Scrapling browser fetch for IMDb."""

from __future__ import annotations

import logging
from typing import Any

from cinemagoerng.web import HTTPClient, set_default_http_client

from db.config import settings
from mediafusion_scrapy.scrapling_adapter import solve_protected_page

logger = logging.getLogger(__name__)

_curl_only_client = HTTPClient()
_imdb_http_client_installed = False


async def _imdb_fetch_with_scrapling_fallback(
    url: str,
    merged_headers: dict[str, str],
    httpx_kwargs: dict[str, Any] | None,
) -> str:
    baseline = _curl_only_client._get_headers(url, {})
    extra = {k: v for k, v in merged_headers.items() if v != baseline.get(k)}
    try:
        return await _curl_only_client.fetch_async(url, headers=extra, httpx_kwargs=httpx_kwargs)
    except Exception as curl_err:
        if not settings.imdb_scrapling_fallback_enabled:
            raise
        logger.warning(
            "IMDb curl_cffi fetch failed for %s; trying Scrapling fallback: %s: %r",
            url,
            type(curl_err).__name__,
            curl_err,
        )
        proxy_url = settings.scrapling_proxy_url or settings.requests_proxy_url
        result = await solve_protected_page(
            url,
            headless=settings.scrapling_headless,
            disable_resources=settings.scrapling_disable_resources,
            network_idle=settings.scrapling_network_idle,
            wait_time_ms=settings.scrapling_wait_time_ms,
            timeout_ms=settings.scrapling_timeout_ms,
            google_search_referer=settings.scrapling_google_search_referer,
            proxy_url=proxy_url,
            cdp_url=settings.scrapling_cdp_url,
            fetcher_mode=settings.scrapling_fetcher_mode,
            solve_cloudflare=settings.scrapling_solve_cloudflare,
            real_chrome=settings.scrapling_real_chrome,
        )
        html = result.get("html") or ""
        status = result.get("status", 0)
        if status != 200 or not str(html).strip():
            raise RuntimeError(
                f"Scrapling IMDb fetch failed for {url!r} (status={status}, empty_body={not str(html).strip()})"
            ) from curl_err
        return str(html)


def install_imdb_cinemagoer_http_client() -> None:
    global _imdb_http_client_installed
    if _imdb_http_client_installed:
        return
    _imdb_http_client_installed = True
    set_default_http_client(HTTPClient(fetch_async_impl=_imdb_fetch_with_scrapling_fallback))
