import base64
from collections.abc import Mapping
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import httpx
from scrapling.fetchers import AsyncStealthySession, DynamicFetcher, StealthyFetcher


def _normalize_cookie_value(raw_value) -> str:
    if raw_value is None:
        return ""
    return str(raw_value)


def normalize_cookies(raw_cookies) -> dict[str, str]:
    if not raw_cookies:
        return {}

    if isinstance(raw_cookies, Mapping):
        return {str(name): _normalize_cookie_value(value) for name, value in raw_cookies.items() if name}

    if isinstance(raw_cookies, (list, tuple)):
        parsed = {}
        for cookie in raw_cookies:
            if isinstance(cookie, Mapping):
                name = cookie.get("name")
                if name:
                    parsed[str(name)] = _normalize_cookie_value(cookie.get("value"))
        return parsed

    if isinstance(raw_cookies, str):
        cookie_jar = SimpleCookie()
        cookie_jar.load(raw_cookies)
        return {key: morsel.value for key, morsel in cookie_jar.items()}

    return {}


def extract_user_agent(response) -> str | None:
    headers = getattr(response, "request_headers", {}) or {}
    if not isinstance(headers, Mapping):
        return None

    for key in ("user-agent", "User-Agent"):
        value = headers.get(key)
        if value:
            return str(value)
    return None


def _coerce_response_body(response) -> str:
    body = getattr(response, "body", "")
    return str(body) if body is not None else ""


def _is_torrent_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return lowered.endswith(".torrent") or ".torrent?" in lowered


def _site_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return url


async def _download_torrent_via_browser_fetch(
    torrent_url: str,
    *,
    referer_url: str,
    headless: bool,
    disable_resources: bool,
    network_idle: bool,
    wait_time_ms: int,
    timeout_ms: int,
    google_search_referer: bool,
    proxy_url: str | None,
    solve_cloudflare: bool,
    real_chrome: bool,
) -> bytes | None:
    page_result: dict[str, str | int] = {}

    async def run_browser_fetch(page):
        result = await page.evaluate(
            """
            async (targetUrl) => {
                const toBase64 = (bytes) => {
                    let binary = '';
                    const chunkSize = 0x8000;
                    for (let i = 0; i < bytes.length; i += chunkSize) {
                        const chunk = bytes.subarray(i, i + chunkSize);
                        binary += String.fromCharCode.apply(null, chunk);
                    }
                    return btoa(binary);
                };

                for (let attempt = 0; attempt < 4; attempt++) {
                    const response = await fetch(targetUrl, { credentials: 'include' });
                    const status = response.status;
                    const contentType = response.headers.get('content-type') || '';
                    const buffer = await response.arrayBuffer();
                    const bytes = new Uint8Array(buffer);

                    if (status === 200 && bytes.length > 0 && bytes[0] === 100) {
                        return {
                            status,
                            contentType,
                            base64: toBase64(bytes),
                        };
                    }

                    const textBody = new TextDecoder().decode(bytes);
                    const match = textBody.match(/form\\.append\\('___ack',\\s*eval\\('([^']+)'\\)\\)/);
                    if (!match) {
                        return {
                            status,
                            contentType,
                            base64: '',
                        };
                    }

                    let ack = null;
                    try {
                        ack = Function(`\"use strict\"; return (${match[1]});`)();
                    } catch (error) {
                        return {
                            status,
                            contentType,
                            base64: '',
                        };
                    }

                    const form = new FormData();
                    form.append('___ack', String(ack));
                    await fetch(targetUrl, { method: 'POST', body: form, credentials: 'include' });
                    await new Promise((resolve) => setTimeout(resolve, 600));
                }

                return { status: 429, contentType: 'text/html', base64: '' };
            }
            """,
            torrent_url,
        )
        if isinstance(result, dict):
            page_result.update(result)

    async with AsyncStealthySession(
        headless=headless,
        disable_resources=disable_resources,
        network_idle=network_idle,
        wait=wait_time_ms,
        timeout=timeout_ms,
        google_search=google_search_referer,
        proxy=proxy_url,
        solve_cloudflare=solve_cloudflare,
        real_chrome=real_chrome,
        block_webrtc=True,
        max_pages=2,
    ) as browser_session:
        await browser_session.fetch(referer_url)
        await browser_session.fetch(referer_url, page_action=run_browser_fetch, wait=0)

    if page_result.get("status") != 200:
        return None

    encoded_payload = page_result.get("base64")
    if not encoded_payload or not isinstance(encoded_payload, str):
        return None

    try:
        return base64.b64decode(encoded_payload)
    except (ValueError, TypeError):
        return None


async def solve_protected_page(
    url: str,
    *,
    headless: bool,
    disable_resources: bool,
    network_idle: bool,
    wait_time_ms: int,
    timeout_ms: int,
    google_search_referer: bool,
    proxy_url: str | None = None,
    fetcher_mode: str = "stealthy",
    solve_cloudflare: bool = False,
    real_chrome: bool = False,
) -> dict:
    use_stealthy = fetcher_mode == "stealthy"
    if use_stealthy:
        response = await StealthyFetcher.async_fetch(
            url=url,
            headless=headless,
            disable_resources=disable_resources,
            network_idle=network_idle,
            wait=wait_time_ms,
            timeout=timeout_ms,
            google_search=google_search_referer,
            proxy=proxy_url,
            solve_cloudflare=solve_cloudflare,
            real_chrome=real_chrome,
            block_webrtc=True,
        )
    else:
        response = await DynamicFetcher.async_fetch(
            url=url,
            headless=headless,
            disable_resources=disable_resources,
            network_idle=network_idle,
            wait=wait_time_ms,
            timeout=timeout_ms,
            google_search=google_search_referer,
            proxy=proxy_url,
        )

    return {
        "url": getattr(response, "url", url),
        "status": getattr(response, "status", 0),
        "html": _coerce_response_body(response),
        "cookies": normalize_cookies(getattr(response, "cookies", {})),
        "user_agent": extract_user_agent(response),
    }


async def download_torrent_with_challenge(
    torrent_url: str,
    *,
    headless: bool,
    disable_resources: bool,
    network_idle: bool,
    wait_time_ms: int,
    timeout_ms: int,
    google_search_referer: bool,
    proxy_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    referer_url: str | None = None,
    fetcher_mode: str = "stealthy",
    solve_cloudflare: bool = False,
    real_chrome: bool = False,
) -> bytes | None:
    if referer_url and not _is_torrent_url(referer_url):
        referer_for_headers = referer_url
    else:
        referer_for_headers = _site_origin(torrent_url)

    if fetcher_mode == "stealthy":
        browser_bytes = await _download_torrent_via_browser_fetch(
            torrent_url,
            referer_url=referer_for_headers,
            headless=headless,
            disable_resources=disable_resources,
            network_idle=network_idle,
            wait_time_ms=wait_time_ms,
            timeout_ms=timeout_ms,
            google_search_referer=google_search_referer,
            proxy_url=proxy_url,
            solve_cloudflare=solve_cloudflare,
            real_chrome=real_chrome,
        )
        if browser_bytes and browser_bytes.startswith(b"d"):
            return browser_bytes

    # Solving the website page first helps challenge-protected direct torrent URLs
    # where opening the .torrent URL in browser context can be unreliable.
    solved_url = referer_for_headers or torrent_url
    solved = await solve_protected_page(
        solved_url,
        headless=headless,
        disable_resources=disable_resources,
        network_idle=network_idle,
        wait_time_ms=wait_time_ms,
        timeout_ms=timeout_ms,
        google_search_referer=google_search_referer,
        proxy_url=proxy_url,
        fetcher_mode=fetcher_mode,
        solve_cloudflare=solve_cloudflare,
        real_chrome=real_chrome,
    )
    cookies: dict[str, str] = {}
    cookies.update(solved.get("cookies", {}))
    user_agent = solved.get("user_agent")

    # Some sites issue anti-bot session cookies only when the .torrent endpoint
    # itself is visited once in a real browser context.
    if _is_torrent_url(torrent_url):
        solved_torrent = await solve_protected_page(
            torrent_url,
            headless=headless,
            disable_resources=disable_resources,
            network_idle=network_idle,
            wait_time_ms=wait_time_ms,
            timeout_ms=timeout_ms,
            google_search_referer=google_search_referer,
            proxy_url=proxy_url,
            fetcher_mode=fetcher_mode,
            solve_cloudflare=solve_cloudflare,
            real_chrome=real_chrome,
        )
        cookies.update(solved_torrent.get("cookies", {}))
        user_agent = solved_torrent.get("user_agent") or user_agent
    headers: dict[str, str] = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    if referer_for_headers:
        headers["Referer"] = referer_for_headers
    headers["Accept"] = "application/x-bittorrent,application/octet-stream;q=0.9,*/*;q=0.8"
    timeout_seconds = max(10, timeout_ms // 1000 + 5)

    if client is not None:
        response = await client.get(
            torrent_url,
            headers=headers,
            cookies=cookies,
            follow_redirects=True,
            timeout=timeout_seconds,
        )
        if response.status_code != 200:
            return None
        return response.content

    async with httpx.AsyncClient(proxy=proxy_url, follow_redirects=True) as temp_client:
        response = await temp_client.get(
            torrent_url,
            headers=headers,
            cookies=cookies,
            timeout=timeout_seconds,
        )
        if response.status_code != 200:
            return None
        return response.content
