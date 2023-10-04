import hashlib
import logging
from urllib.parse import quote

import PTN
import bencodepy

from utils.parser import clean_name

TRACKERS = [
    "udp://tracker.openbittorrent.com:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.pomf.se:80/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.internetwarriors.net:1337/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://tracker.coppersurfer.tk:6969/announce",
    "udp://tracker.leechers-paradise.org:6969/announce",
    "udp://9.rarbg.to:2710/announce",
    "udp://9.rarbg.me:2710/announce",
    "http://tracker3.itzmx.com:8080/announce",
    "udp://ipv4.tracker.harry.lu:80/announce",
]


def extract_torrent_metadata(content) -> dict:
    try:
        torrent_data = bencodepy.decode(content)

        info = torrent_data[b"info"]
        info_encoded = bencodepy.encode(info)

        m = hashlib.sha1()
        m.update(info_encoded)
        info_hash = m.hexdigest()

        # Extract file size, file list, and announce list
        if b"files" in info:
            total_size = sum(file[b"length"] for file in info[b"files"])
            file_data = []
            for idx, file in enumerate(info[b"files"]):
                filename = file[b"path"][0].decode()
                parsed_data = PTN.parse(filename)
                file_data.append(
                    {
                        "filename": filename,
                        "size": file[b"length"],
                        "index": idx,
                        "season": parsed_data.get("season"),
                        "episode": parsed_data.get("episode"),
                    }
                )
        else:
            total_size = info[b"length"]
            filename = info[b"name"].decode()
            parsed_data = PTN.parse(filename)
            file_data = [
                {
                    "filename": filename,
                    "size": total_size,
                    "index": 0,
                    "season": parsed_data.get("season"),
                    "episode": parsed_data.get("episode"),
                }
            ]

        announce_list = [
            tracker[0].decode() for tracker in torrent_data.get(b"announce-list", [])
        ]
        torrent_name = info.get(b"name", b"").decode() or file_data[0]["filename"]

        return {
            "info_hash": info_hash,
            "announce_list": announce_list,
            "total_size": total_size,
            "file_data": file_data,
            "torrent_name": torrent_name,
        }

    except Exception as e:
        logging.error(f"Error occurred: {e}")
        return {}


def convert_info_hash_to_magnet(info_hash: str, trackers: list[str], name: str) -> str:
    magnet_link = f"magnet:?xt=urn:btih:{info_hash}"
    encoded_name = quote(clean_name(name), safe="")
    magnet_link += f"&dn={encoded_name}"
    for tracker in trackers or TRACKERS:
        encoded_tracker = quote(tracker, safe="")
        magnet_link += f"&tr={encoded_tracker}"
    return magnet_link
