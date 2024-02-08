import re

import httpx
from bs4 import BeautifulSoup


async def get_torrent_info(url: str) -> dict:
    torrent_info = {}
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            # find the torrent link with regex of '.torrent' in the <a> href and child <img> having alt="Download torrent".
            torrent_links = soup.find_all("a", href=re.compile(".torrent"))
            for link in torrent_links:
                if link.find("img", alt="Download torrent"):
                    torrent_info["downloadUrl"] = link.get("href").replace(
                        "http://", "https://"
                    )

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
