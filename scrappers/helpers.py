import json
import logging

import PTN
import cloudscraper
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db import crud
from utils.torrent import extract_torrent_metadata


def get_scrapper_session(proxy_url=None):
    session = requests.session()
    session.headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    }
    adapter = HTTPAdapter(
        max_retries=Retry(total=10, read=10, connect=10, backoff_factor=0.5)
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if proxy_url:
        session.proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
        delay=10,
        sess=session,
    )
    return scraper


async def check_cloudflare_validation(page):
    if await page.title() == "Just a moment...":
        logging.info("Cloudflare validation required")
        await page.wait_for_selector("a#elUserSignIn", timeout=9999999)


async def get_page_content(page, url):
    await page.goto(url)
    await check_cloudflare_validation(page)
    return await page.content()


async def download_and_save_torrent(
    torrent_element,
    metadata: dict,
    media_type: str,
    page_link: str,
    scraper=None,
    page=None,
):
    torrent_link = torrent_element.get("href")
    logging.info(f"Downloading torrent: {torrent_link}")

    if scraper:
        response = scraper.get(torrent_link)
        torrent_metadata = extract_torrent_metadata(response.content)
    elif page:
        async with page.expect_download() as download_info:
            try:
                await page.goto(torrent_link)
            except:
                pass
        download = await download_info.value
        torrent_path = await download.path()
        with open(torrent_path, "rb") as torrent_file:
            torrent_metadata = extract_torrent_metadata(torrent_file.read())

    if not torrent_metadata:
        logging.error(f"Info hash not found for {torrent_link}")
        return False

    metadata.update(torrent_metadata)

    if not metadata.get("year"):
        logging.error(f"Year not found for {page_link}")
        return False

    # Saving the metadata
    if media_type == "series":
        if not metadata.get("season"):
            logging.error(f"Season not found for {page_link}")
            return False
        await crud.save_series_metadata(metadata)
    else:
        if metadata.get("season"):
            await crud.save_series_metadata(metadata)
        else:
            await crud.save_movie_metadata(metadata)

    return True


def get_scrapper_config(site_name: str, get_key: str) -> dict:
    with open("resources/json/scrapper_config.json") as file:
        config = json.load(file)

    return config.get(site_name, {}).get(get_key, {})
