import re

from scrapers.helpers import get_page_bs4


async def get_torrent_info(url: str, indexer: str) -> dict:
    torrent_info = {}
    parse_func = parser_function_map.get(indexer)
    if not parse_func:
        return torrent_info

    soup = await get_page_bs4(url)
    if not soup:
        return torrent_info

    torrent_info = parse_func(soup, torrent_info)
    return torrent_info


def parse_1337x(soup, torrent_info):
    magnet_link = soup.find("a", href=re.compile("magnet:?"))
    if magnet_link:
        torrent_info["magnetUrl"] = magnet_link.get("href")
        torrent_info["downloadUrl"] = None

    info_hash = soup.find("div", class_="infohash-box").find("span")
    if info_hash:
        torrent_info["infoHash"] = info_hash.text.strip()

    return torrent_info


def parse_therarbg(soup, torrent_info):
    magnet_links = soup.find_all("a", href=re.compile("magnet:?"))
    for link in magnet_links:
        torrent_info["magnetUrl"] = link.get("href")
        torrent_info["downloadUrl"] = None

    info_hash = soup.find("td", string=re.compile("[A-Fa-f0-9]{40}"))
    if info_hash:
        torrent_info["infoHash"] = info_hash.text.strip()

    return torrent_info


def parse_torrent_downloads(soup, torrent_info):
    magnet_links = soup.find_all("a", href=re.compile("magnet:?"))
    for link in magnet_links:
        torrent_info["magnetUrl"] = link.get("href")
        torrent_info["downloadUrl"] = None

    info_hash_span = soup.find("div", id="main_wrapper").find(
        "span", string=re.compile("Infohash:")
    )
    if info_hash_span:
        info_hash = (
            info_hash_span.find_parent("p").text.replace("Infohash:", "").strip()
        )
        torrent_info["infoHash"] = info_hash.strip()

    return torrent_info


def parse_badass_torrents(soup, torrent_info):
    magnet_link = soup.find("a", href=re.compile("magnet:?"))
    if magnet_link:
        torrent_info["magnetUrl"] = magnet_link.get("href")
        torrent_info["downloadUrl"] = None

    info_hash = soup.find("td", string=re.compile("[A-Fa-f0-9]{40}"))
    if info_hash:
        torrent_info["infoHash"] = info_hash.text.strip()

    return torrent_info


parser_function_map = {
    "1337x": parse_1337x,
    "TheRARBG": parse_therarbg,
    "Torrent Downloads": parse_torrent_downloads,
    "Badass Torrents": parse_badass_torrents,
}
