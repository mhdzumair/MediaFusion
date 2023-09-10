import bencodepy
import hashlib
import logging
import requests
from urllib.parse import quote

from utils.site_data import TRACKERS


def get_info_hash_from_url(torrent_url):
    try:
        response = requests.get(torrent_url)
        response.raise_for_status()

        torrent_data = bencodepy.decode(response.content)

        info = torrent_data[b"info"]
        info_encoded = bencodepy.encode(info)

        m = hashlib.sha1()
        m.update(info_encoded)
        return m.hexdigest()

    except Exception as e:
        logging.error(f"Error occurred: {e}")
        return False


def convert_info_hash_to_magnet(info_hash: str, name: str = "") -> str:
    magnet_link = f"magnet:?xt=urn:btih:{info_hash}"
    if name:
        encoded_name = quote(name, safe="")
        magnet_link += f"&dn={encoded_name}"
    for tracker in TRACKERS:
        encoded_tracker = quote(tracker, safe="")
        magnet_link += f"&tr={encoded_tracker}"
    return magnet_link
