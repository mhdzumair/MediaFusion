import re
from urllib.parse import urljoin

from scrapers.helpers import get_page_bs4
from utils.torrent import get_info_hash_from_magnet


async def get_torrent_info(url: str, indexer: str) -> dict:
    torrent_info = {}
    pre_process_func = pre_process_map.get(indexer, get_page_bs4)
    parse_func = parser_function_map.get(indexer, parse_common_torrents)

    soup = await pre_process_func(url)
    if not soup:
        return torrent_info

    torrent_info = parse_func(soup, torrent_info, url)
    return torrent_info


def parse_1337x(soup, torrent_info, url):
    magnet_link = soup.find("a", href=re.compile("magnet:?"))
    if magnet_link:
        torrent_info["magnetUrl"] = magnet_link.get("href")
        torrent_info["downloadUrl"] = None

    info_hash = soup.find("div", class_="infohash-box").find("span")
    if info_hash:
        torrent_info["infoHash"] = info_hash.text.strip()

    return torrent_info


def parse_torrent_downloads(soup, torrent_info, url):
    magnet_link = soup.find("a", href=re.compile("magnet:?"))
    if magnet_link:
        torrent_info["magnetUrl"] = magnet_link.get("href")
        torrent_info["downloadUrl"] = None

    info_hash_span = soup.find("div", id="main_wrapper").find("span", string=re.compile("Infohash:"))
    if info_hash_span:
        info_hash = info_hash_span.find_parent("p").text.replace("Infohash:", "").strip()
        torrent_info["infoHash"] = info_hash

    return torrent_info


def parse_torrentdownloads(soup, torrent_info, url):
    info_hash = soup.find("td", string=re.compile("[A-Fa-f0-9]{40}"))
    if info_hash:
        torrent_info["infoHash"] = info_hash.text.strip()

    torrent_download_link = soup.find("a", href=re.compile("/td.php?"))
    magnet_link = soup.find("a", href=re.compile("magnet:?"))
    if torrent_download_link:
        torrent_info["downloadUrl"] = urljoin(url, torrent_download_link.get("href"))
        torrent_info["magnetUrl"] = None
    elif magnet_link:
        torrent_info["magnetUrl"] = magnet_link.get("href")
        torrent_info["downloadUrl"] = None

    return torrent_info


def parse_badass_torrents(soup, torrent_info, url):
    info_hash = soup.find("td", string=re.compile("[A-Fa-f0-9]{40}"))
    if info_hash:
        torrent_info["infoHash"] = info_hash.text.strip()

    magnet_link = soup.find("a", href=re.compile("magnet:?"))
    torrent_link = soup.find("a", string="Torrent Download")
    if torrent_link:
        torrent_info["downloadUrl"] = urljoin(url, torrent_link.get("href"))
    if magnet_link:
        torrent_info["magnetUrl"] = magnet_link.get("href")
    return torrent_info


def parse_common_torrents(soup, torrent_info, url):
    magnet_link = soup.find("a", href=re.compile("magnet:?"))
    if magnet_link:
        torrent_info["magnetUrl"] = magnet_link.get("href")
        torrent_info["downloadUrl"] = None
        torrent_info["infoHash"] = get_info_hash_from_magnet(magnet_link.get("href"))
    return torrent_info


def parse_itorrent(soup, torrent_info, url):
    magnet_link = soup.find("a", href=re.compile("magnet:?"))
    if magnet_link:
        torrent_info["infoHash"] = get_info_hash_from_magnet(magnet_link.get("href"))

    download_link = soup.find("a", class_="jq-download")
    if download_link:
        torrent_info["magnetUrl"] = None
        torrent_info["downloadUrl"] = urljoin(url, download_link.get("href"))

    return torrent_info


def parse_gktorrent(soup, torrent_info, url):
    magnet_link = soup.find("a", href=re.compile("magnet:?"))
    if magnet_link:
        torrent_info["magnetUrl"] = None
        torrent_info["infoHash"] = get_info_hash_from_magnet(magnet_link.get("href"))

    download_link = soup.find("div", class_="btn-download").find("a")
    if download_link:
        torrent_info["downloadUrl"] = urljoin(url, download_link.get("href"))

    return torrent_info


async def pre_process_therarbg(url):
    url = url.replace("?format=json", "")
    return await get_page_bs4(url)


pre_process_map = {
    "TheRARBG": pre_process_therarbg,
}

parser_function_map = {
    "1337x": parse_1337x,
    "TheRARBG": parse_common_torrents,
    "Torrent Downloads": parse_torrent_downloads,
    "Badass Torrents": parse_badass_torrents,
    "iTorrent": parse_itorrent,
    "TorrentDownloads": parse_torrentdownloads,
}
