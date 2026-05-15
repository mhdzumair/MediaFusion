import base64
import argparse
import asyncio
from collections.abc import Mapping
from http.cookies import SimpleCookie
from urllib.parse import urljoin, urlsplit

import httpx
from parsel import Selector
from scrapling.fetchers import AsyncStealthySession


def normalize_cookies(raw_cookies) -> dict[str, str]:
    if not raw_cookies:
        return {}

    if isinstance(raw_cookies, Mapping):
        return {str(name): str(value) for name, value in raw_cookies.items() if name}

    if isinstance(raw_cookies, (list, tuple)):
        parsed: dict[str, str] = {}
        for cookie in raw_cookies:
            if isinstance(cookie, Mapping):
                name = cookie.get("name")
                if name:
                    parsed[str(name)] = str(cookie.get("value") or "")
        return parsed

    if isinstance(raw_cookies, str):
        jar = SimpleCookie()
        jar.load(raw_cookies)
        return {key: morsel.value for key, morsel in jar.items()}

    return {}


def response_text(body) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="ignore")
    return str(body)


def extract_user_agent(response) -> str | None:
    headers = getattr(response, "request_headers", {}) or {}
    if not isinstance(headers, Mapping):
        return None
    for key in ("user-agent", "User-Agent"):
        value = headers.get(key)
        if value:
            return str(value)
    return None


def site_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return url


async def resolve_sample_torrent(start_url: str) -> tuple[str, str]:
    async with AsyncStealthySession(
        headless=True,
        block_webrtc=True,
        solve_cloudflare=True,
        timeout=120000,
        max_pages=2,
        network_idle=False,
        wait=4000,
        google_search=True,
    ) as session:
        listing_response = await session.fetch(start_url)
        listing_html = response_text(getattr(listing_response, "body", ""))
        listing_selector = Selector(text=listing_html)

        direct_torrent = listing_selector.css("a[href$='.torrent']::attr(href)").get()
        if direct_torrent:
            torrent_url = urljoin(start_url, direct_torrent)
            referer_url = site_origin(torrent_url)
            return torrent_url, referer_url

        detail_href = listing_selector.css("div[id^='wb_Shape'] a::attr(href)").get()
        if not detail_href:
            raise RuntimeError("Could not find a sample detail page on Sport Video listing.")

        detail_url = urljoin(start_url, detail_href)
        detail_response = await session.fetch(detail_url)
        detail_html = response_text(getattr(detail_response, "body", ""))
        detail_selector = Selector(text=detail_html)
        torrent_href = detail_selector.css("a[href$='.torrent']::attr(href)").get()
        if not torrent_href:
            raise RuntimeError("Could not find a .torrent link on Sport Video detail page.")

        torrent_url = urljoin(detail_url, torrent_href)
        referer_url = detail_url
        return torrent_url, referer_url


async def test_torrent_download(start_url: str) -> None:
    torrent_url, referer_url = await resolve_sample_torrent(start_url)
    print(f"Sample torrent URL: {torrent_url}")
    print(f"Referer solve URL: {referer_url}")

    browser_result: dict[str, str | int] = {}

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
                        return { status, contentType, base64: toBase64(bytes) };
                    }

                    const textBody = new TextDecoder().decode(bytes);
                    const match = textBody.match(/form\\.append\\('___ack',\\s*eval\\('([^']+)'\\)\\)/);
                    if (!match) {
                        return { status, contentType, base64: '' };
                    }

                    let ack = null;
                    try {
                        ack = Function(`\"use strict\"; return (${match[1]});`)();
                    } catch (error) {
                        return { status, contentType, base64: '' };
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
            browser_result.update(result)

    async with AsyncStealthySession(
        headless=True,
        block_webrtc=True,
        solve_cloudflare=True,
        timeout=120000,
        max_pages=2,
        network_idle=False,
        wait=4000,
        google_search=True,
    ) as session:
        solved_referer = await session.fetch(referer_url)
        await session.fetch(referer_url, page_action=run_browser_fetch, wait=0)
        cookies = normalize_cookies(getattr(solved_referer, "cookies", {}))
        user_agent = extract_user_agent(solved_referer)

    browser_bytes = b""
    if browser_result.get("status") == 200 and isinstance(browser_result.get("base64"), str):
        try:
            browser_bytes = base64.b64decode(browser_result["base64"])
        except (ValueError, TypeError):
            browser_bytes = b""
    print(f"Browser fetch status: {browser_result.get('status')}")
    print(f"Browser fetch content-type: {browser_result.get('contentType')}")
    print(f"Browser payload bytes: {len(browser_bytes)}")
    print(f"Browser payload bencode: {browser_bytes.startswith(b'd')}")
    if browser_bytes.startswith(b"d"):
        return

    headers: dict[str, str] = {
        "Accept": "application/x-bittorrent,application/octet-stream;q=0.9,*/*;q=0.8",
        "Referer": referer_url,
    }
    if user_agent:
        headers["User-Agent"] = user_agent

    async with httpx.AsyncClient(follow_redirects=True, timeout=90) as client:
        response = await client.get(torrent_url, headers=headers, cookies=cookies)

    print(f"HTTP status: {response.status_code}")
    print(f"Content-Type: {response.headers.get('content-type')}")
    print(f"Payload bytes: {len(response.content)}")
    print(f"Starts with bencode dict marker: {response.content.startswith(b'd')}")

    if response.status_code != 200 or not response.content.startswith(b"d"):
        raise RuntimeError("Failed to download a valid .torrent payload from Sport Video.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Sport Video torrent download via Scrapling only.")
    parser.add_argument(
        "--start-url",
        default="https://www.sport-video.org.ua/other.html",
        help="Sport Video category/listing URL to sample.",
    )
    args = parser.parse_args()
    asyncio.run(test_torrent_download(args.start_url))


if __name__ == "__main__":
    main()
