import re

import httpx
from bs4 import BeautifulSoup


async def get_torrent_info(url: str) -> dict:
    torrent_info = {}
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            # find the torrent link with regex of '.torrent' in the <a> href and child <i> having 'fa-download' class
            torrent_links = soup.find_all("a", href=re.compile(".torrent"))
            for link in torrent_links:
                if link.find("i", class_="fa-download"):
                    torrent_info["downloadUrl"] = link.get("href").replace(
                        "http://", "https://"
                    )

            # find the magnet link with regex of 'magnet:?' in the <a> href
            magnet_links = soup.find_all("a", href=re.compile("magnet:?"))
            for link in magnet_links:
                torrent_info["magnetUrl"] = link.get("href")

            # find the torrent info hash with regex of info hash length 40 from <td> text
            info_hash = soup.find("td", text=re.compile("[A-Fa-f0-9]{40}"))
            if info_hash:
                torrent_info["infoHash"] = info_hash.text.strip()

    return torrent_info
