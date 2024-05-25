import re

import httpx
from bs4 import BeautifulSoup

from utils.const import UA_HEADER


async def get_torrent_info(url: str) -> dict:
    torrent_info = {}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=UA_HEADER)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            # itorrents.org have rate limit, so we are using a magnet link instead of torrent file
            torrent_info["downloadUrl"] = None

            magnet_links = soup.find_all("a", href=re.compile("magnet:?"))
            for link in magnet_links:
                torrent_info["magnetUrl"] = link.get("href")

            # find the torrent info hash id of div 'main_wrapper' with <span> having text "Infohash:" and parent <p> tag
            info_hash_span = soup.find("div", id="main_wrapper").find(
                "span", string=re.compile("Infohash:")
            )
            if info_hash_span:
                info_hash = (
                    info_hash_span.find_parent("p")
                    .text.replace("Infohash:", "")
                    .strip()
                )
                torrent_info["infoHash"] = info_hash.strip()

    return torrent_info
