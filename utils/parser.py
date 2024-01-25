import asyncio
import math
import re

import requests
from imdb import Cinemagoer

from db.config import settings
from db.models import TorrentStreams, MediaFusionTVMetaData
from db.schemas import Stream, UserData
from streaming_providers.alldebrid.utils import (
    update_ad_cache_status,
    fetch_downloaded_info_hashes_from_ad,
)
from streaming_providers.debridlink.utils import (
    update_dl_cache_status,
    fetch_downloaded_info_hashes_from_dl,
)
from streaming_providers.offcloud.utils import (
    update_oc_cache_status,
    fetch_downloaded_info_hashes_from_oc,
)
from streaming_providers.pikpak.utils import (
    update_pikpak_cache_status,
    fetch_downloaded_info_hashes_from_pikpak,
)
from streaming_providers.premiumize.utils import (
    update_pm_cache_status,
    fetch_downloaded_info_hashes_from_premiumize,
)
from streaming_providers.realdebrid.utils import (
    update_rd_cache_status,
    fetch_downloaded_info_hashes_from_rd,
)
from streaming_providers.seedr.utils import (
    update_seedr_cache_status,
    fetch_downloaded_info_hashes_from_seedr,
)
from streaming_providers.torbox.utils import (
    update_torbox_cache_status,
    fetch_downloaded_info_hashes_from_torbox,
)

ia = Cinemagoer()


async def filter_and_sort_streams(
    streams: list[TorrentStreams], user_data: UserData
) -> list[TorrentStreams]:
    # Filter streams by selected catalogs and resolutions
    filtered_streams = [
        stream
        for stream in streams
        if any(catalog in stream.catalog for catalog in user_data.selected_catalogs)
        and stream.resolution in user_data.selected_resolutions
        and stream.size <= user_data.max_size
    ]

    if not filtered_streams:
        return []

    # Define provider-specific cache update functions
    cache_update_functions = {
        "alldebrid": update_ad_cache_status,
        "debridlink": update_dl_cache_status,
        "offcloud": update_oc_cache_status,
        "pikpak": update_pikpak_cache_status,
        "realdebrid": update_rd_cache_status,
        "seedr": update_seedr_cache_status,
        "torbox": update_torbox_cache_status,
        "premiumize": update_pm_cache_status,
    }

    # Update cache status based on provider
    if user_data.streaming_provider:
        if cache_update_function := cache_update_functions.get(
            user_data.streaming_provider.service
        ):
            if asyncio.iscoroutinefunction(cache_update_function):
                await cache_update_function(streams, user_data)
            else:
                await asyncio.to_thread(cache_update_function, streams, user_data)

    # Sort streams by cache status, creation date, and size
    return sorted(
        filtered_streams, key=lambda x: (x.cached, x.size, x.created_at), reverse=True
    )


async def parse_stream_data(
    streams: list[TorrentStreams],
    user_data: UserData,
    secret_str: str,
    season: int = None,
    episode: int = None,
) -> list[Stream]:
    stream_list = []

    streams = await filter_and_sort_streams(streams, user_data)

    for stream_data in streams:
        quality_detail = " - ".join(
            filter(
                None,
                [
                    stream_data.quality,
                    stream_data.resolution,
                    stream_data.codec,
                    stream_data.audio,
                ],
            )
        )

        episode_data = stream_data.get_episode(season, episode)

        if user_data.streaming_provider:
            streaming_provider = user_data.streaming_provider.service.title()
            if stream_data.cached:
                streaming_provider += " ⚡️"
            else:
                streaming_provider += " ⏳"
        else:
            streaming_provider = "Torrent ⏳"

        seeders = f"👤 {stream_data.seeders}" if stream_data.seeders else None

        description_parts = [
            quality_detail,
            convert_bytes_to_readable(
                episode_data.size if episode_data else stream_data.size
            ),
            seeders,
            " + ".join(stream_data.languages),
            stream_data.source,
        ]
        description = ", ".join(filter(lambda x: bool(x), description_parts))

        stream_details = {
            "name": f"MediaFusion {streaming_provider}",
            "description": description,
            "infoHash": stream_data.id,
            "fileIdx": episode_data.file_index
            if episode_data
            else stream_data.file_index,
            "behaviorHints": {"bingeGroup": f"MediaFusion-{quality_detail}"},
        }

        if user_data.streaming_provider:
            base_proxy_url = f"{settings.host_url}/{secret_str}/streaming_provider?info_hash={stream_data.id}"
            if episode_data:
                base_proxy_url += f"&season={season}&episode={episode}"
            stream_details["url"] = base_proxy_url
            stream_details.pop("infoHash")
            stream_details.pop("fileIdx")
            stream_details["behaviorHints"]["notWebReady"] = True

        stream_list.append(Stream(**stream_details))

    return stream_list


def convert_bytes_to_readable(size_bytes: int) -> str:
    """
    Convert a size in bytes into a more human-readable format.
    """
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"💾 {s} {size_name[i]}"


def convert_size_to_bytes(size_str: str) -> int:
    """Convert size string to bytes."""
    match = re.match(r"(\d+(?:\.\d+)?)\s*(GB|MB)", size_str, re.IGNORECASE)
    if match:
        size, unit = match.groups()
        size = float(size)
        return int(size * 1024**3) if "GB" in unit.upper() else int(size * 1024**2)
    return 0


def get_catalogs(catalog: str, languages: list[str]) -> list[str]:
    base_catalogs = ["hdrip", "tcrip", "dubbed", "series"]
    base_catalog = catalog.split("_")[1]

    if base_catalog not in base_catalogs:
        return [catalog]

    # Generate the catalog for each supported language
    return [f"{lang.lower()}_{base_catalog}" for lang in languages]


def search_imdb(title: str, year: int, retry: int = 5) -> dict:
    try:
        result = ia.search_movie(f"{title} {year}")
    except Exception:
        return search_imdb(title, year, retry - 1) if retry > 0 else {}
    for movie in result:
        if movie.get("year") == year and movie.get("title").lower() in title.lower():
            imdb_id = f"tt{movie.movieID}"
            poster = f"https://live.metahub.space/poster/small/{imdb_id}/img"
            if requests.get(poster).status_code == 200:
                return {
                    "imdb_id": imdb_id,
                    "poster": poster.replace("small", "medium"),
                    "background": f"https://live.metahub.space/background/medium/{imdb_id}/img",
                    "title": movie.myTitle,
                }
            poster = movie.get("full-size cover url")
            return {
                "imdb_id": imdb_id,
                "poster": poster,
                "background": poster,
                "title": movie.myTitle,
            }
    return {}


def get_imdb_title(video_id:str) -> str:
    movie = ia.get_movie(video_id)
    return movie.get("title")


def parse_tv_stream_data(tv_data: MediaFusionTVMetaData) -> list[Stream]:
    stream_list = []
    for stream in tv_data.streams:
        if stream.behaviorHints.get("is_redirect", False):
            response = requests.get(
                stream.url,
                headers=stream.behaviorHints["proxyHeaders"]["request"],
                allow_redirects=False,
            )
            if response.status_code == 302:
                stream.url = response.headers["Location"]
        stream_list.append(
            Stream(
                name="MediaFusion",
                description=f"{stream.name}, {tv_data.tv_language}, {stream.source}",
                url=stream.url,
                ytId=stream.ytId,
                behaviorHints=stream.behaviorHints,
            )
        )

    return stream_list


async def fetch_downloaded_info_hashes(user_data: UserData) -> list[str]:
    fetch_downloaded_info_hashes_functions = {
        "alldebrid": fetch_downloaded_info_hashes_from_ad,
        "debridlink": fetch_downloaded_info_hashes_from_dl,
        "offcloud": fetch_downloaded_info_hashes_from_oc,
        "pikpak": fetch_downloaded_info_hashes_from_pikpak,
        "realdebrid": fetch_downloaded_info_hashes_from_rd,
        "seedr": fetch_downloaded_info_hashes_from_seedr,
        "torbox": fetch_downloaded_info_hashes_from_torbox,
        "premiumize": fetch_downloaded_info_hashes_from_premiumize,
    }

    if fetch_downloaded_info_hashes_function := fetch_downloaded_info_hashes_functions.get(
        user_data.streaming_provider.service
    ):
        if asyncio.iscoroutinefunction(fetch_downloaded_info_hashes_function):
            downloaded_info_hashes = await fetch_downloaded_info_hashes_function(
                user_data
            )
        else:
            downloaded_info_hashes = await asyncio.to_thread(
                fetch_downloaded_info_hashes_function, user_data
            )

        return downloaded_info_hashes

    return []


def generate_manifest(manifest: dict, user_data: UserData) -> dict:
    resources = manifest["resources"]
    if user_data.enable_catalogs:
        manifest["catalogs"] = [
            cat
            for cat in manifest["catalogs"]
            if cat["id"] in user_data.selected_catalogs
        ]
    else:
        manifest["catalogs"] = []
        resources = [
            {
                "name": "stream",
                "types": ["movie", "series", "tv"],
                "idPrefixes": ["tt", "mf"],
            }
        ]

    if user_data.streaming_provider:
        provider_name = user_data.streaming_provider.service.title()
        manifest["name"] += f" {provider_name}"
        manifest["id"] += f".{provider_name.lower()}"

        if user_data.streaming_provider.enable_watchlist_catalogs:
            watchlist_catalogs = [
                {
                    "id": f"{provider_name.lower()}_watchlist_movies",
                    "name": f"{provider_name} Watchlist",
                    "type": "movie",
                    "extra": [{"name": "skip", "isRequired": False}],
                },
                {
                    "id": f"{provider_name.lower()}_watchlist_series",
                    "name": f"{provider_name} Watchlist",
                    "type": "series",
                    "extra": [{"name": "skip", "isRequired": False}],
                },
            ]
            manifest["catalogs"] = watchlist_catalogs + manifest["catalogs"]
            resources = manifest["resources"]

    manifest["resources"] = resources
    return manifest
