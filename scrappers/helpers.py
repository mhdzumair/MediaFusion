import json
import logging
from datetime import datetime

import cloudscraper
import dramatiq
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db.config import settings
from db.models import TorrentStreams, Episode, Season
from utils.torrent import extract_torrent_metadata, info_hashes_to_torrent_metadata

UA_HEADER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
}
PROXIES = (
    {
        "http": settings.scrapper_proxy_url,
        "https": settings.scrapper_proxy_url,
    }
    if settings.scrapper_proxy_url
    else None
)


def get_scrapper_session():
    session = requests.session()
    session.headers = UA_HEADER
    adapter = HTTPAdapter(
        max_retries=Retry(total=10, read=10, connect=10, backoff_factor=0.5)
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.proxies = PROXIES
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


async def download_torrent(torrent_link, scraper=None, page=None):
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
        return None

    return torrent_metadata


async def save_torrent(torrent_metadata: dict, metadata: dict, media_type: str):
    metadata.update(torrent_metadata)

    if not metadata.get("year"):
        logging.error("Year not found")
        return False

    from db import crud  # Avoid circular import

    # Saving the metadata
    if media_type == "series":
        if not metadata.get("season"):
            logging.error("Season not found")
            return False
        await crud.save_series_metadata(metadata)
    else:
        if metadata.get("season"):
            await crud.save_series_metadata(metadata)
        else:
            await crud.save_movie_metadata(metadata)

    return True


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
    torrent_metadata = await download_torrent(torrent_link, scraper, page)
    if torrent_metadata is None:
        return False

    result = await save_torrent(torrent_metadata, metadata, media_type)
    if not result:
        logging.error(f"Failed to save torrent for {page_link}")
    return result


def get_scrapper_config(site_name: str, get_key: str) -> dict:
    with open("resources/json/scrapper_config.json") as file:
        config = json.load(file)

    return config.get(site_name, {}).get(get_key, {})


@dramatiq.actor(time_limit=30 * 60 * 1000)
async def update_torrent_movie_streams_metadata(info_hashes: list[str]):
    """Update torrent streams metadata."""
    if not info_hashes:
        return

    streams_metadata = await info_hashes_to_torrent_metadata(info_hashes, [])

    for stream_metadata in streams_metadata:
        if not stream_metadata:
            continue

        torrent_stream = await TorrentStreams.get(stream_metadata["info_hash"])
        if torrent_stream:
            torrent_stream.torrent_name = stream_metadata["torrent_name"]
            torrent_stream.size = stream_metadata["total_size"]

            torrent_stream.filename = stream_metadata["largest_file"]["filename"]
            torrent_stream.file_index = stream_metadata["largest_file"]["index"]
            torrent_stream.updated_at = datetime.now()
            await torrent_stream.save()
            logging.info(f"Updated {torrent_stream.id} metadata")


@dramatiq.actor(time_limit=30 * 60 * 1000)
async def update_torrent_series_streams_metadata(info_hashes: list[str]):
    """Update torrent streams metadata."""
    if not info_hashes:
        return

    streams_metadata = await info_hashes_to_torrent_metadata(info_hashes, [])

    for stream_metadata in streams_metadata:
        if not stream_metadata:
            continue

        torrent_stream = await TorrentStreams.get(stream_metadata["info_hash"])
        if torrent_stream:
            episodes = [
                Episode(
                    episode_number=file["episode"],
                    filename=file["filename"],
                    size=file["size"],
                    file_index=file["index"],
                )
                for file in stream_metadata["file_data"]
                if file["episode"]
            ]
            torrent_stream.season = Season(
                season_number=stream_metadata["season"],
                episodes=episodes,
            )

            torrent_stream.torrent_name = stream_metadata["torrent_name"]
            torrent_stream.size = stream_metadata["total_size"]

            torrent_stream.updated_at = datetime.now()

            await torrent_stream.save()
            logging.info(f"Updated {torrent_stream.id} metadata")
