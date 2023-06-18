import hashlib
import logging

import bencodepy
import requests


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
