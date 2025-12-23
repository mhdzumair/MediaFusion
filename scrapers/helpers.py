import json
import logging
from datetime import datetime

import dramatiq
from bs4 import BeautifulSoup
import httpx

from db.config import settings
from db.schemas import TorrentStreamData, EpisodeFileData
from utils.torrent import info_hashes_to_torrent_metadata

# set httpx logging level
logging.getLogger("httpx").setLevel(logging.WARNING)


def get_country_name(country_code):
    with open("resources/json/countries.json") as file:
        countries = json.load(file)
    return countries.get(country_code.upper(), "India")


async def get_page_bs4(url: str):
    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as session:
            response = await session.get(url, timeout=10)
            if response.status_code != 200:
                return None
            return BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        logging.error(f"Error fetching page: {url}, error: {e}")
        return None
