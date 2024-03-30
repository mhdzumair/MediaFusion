import asyncio
import json
import math
import re

import requests
from imdb import Cinemagoer
from redis.asyncio import Redis

from db.config import settings
from db.models import TorrentStreams, TVStreams
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
from streaming_providers.qbittorrent.utils import (
    update_qbittorrent_cache_status,
    fetch_info_hashes_from_webdav,
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
from utils import const
from utils.network import get_redirector_url
from utils.validation_helper import validate_m3u8_url

ia = Cinemagoer()
ADULT_CONTENT_KEYWORDS = re.compile(
    settings.adult_content_regex_keywords,
    re.IGNORECASE,
)
# Define provider-specific cache update functions
CACHE_UPDATE_FUNCTIONS = {
    "alldebrid": update_ad_cache_status,
    "debridlink": update_dl_cache_status,
    "offcloud": update_oc_cache_status,
    "pikpak": update_pikpak_cache_status,
    "realdebrid": update_rd_cache_status,
    "seedr": update_seedr_cache_status,
    "torbox": update_torbox_cache_status,
    "premiumize": update_pm_cache_status,
    "qbittorrent": update_qbittorrent_cache_status,
}

# Define provider-specific downloaded info hashes fetch functions
FETCH_DOWNLOADED_INFO_HASHES_FUNCTIONS = {
    "alldebrid": fetch_downloaded_info_hashes_from_ad,
    "debridlink": fetch_downloaded_info_hashes_from_dl,
    "offcloud": fetch_downloaded_info_hashes_from_oc,
    "pikpak": fetch_downloaded_info_hashes_from_pikpak,
    "realdebrid": fetch_downloaded_info_hashes_from_rd,
    "seedr": fetch_downloaded_info_hashes_from_seedr,
    "torbox": fetch_downloaded_info_hashes_from_torbox,
    "premiumize": fetch_downloaded_info_hashes_from_premiumize,
    "qbittorrent": fetch_info_hashes_from_webdav,
}


async def filter_and_sort_streams(
    streams: list[TorrentStreams], user_data: UserData
) -> list[TorrentStreams]:
    # Convert to sets for faster lookups
    selected_catalogs_set = set(user_data.selected_catalogs)
    selected_resolutions_set = set(user_data.selected_resolutions)

    # Step 1: Filter streams by selected catalogs, resolutions, and size
    filtered_streams = [
        stream
        for stream in streams
        if any(catalog_id in selected_catalogs_set for catalog_id in stream.catalog)
        and stream.resolution in selected_resolutions_set
        and stream.size <= user_data.max_size
    ]

    if not filtered_streams:
        return []

    # Step 2: Update cache status based on provider
    cache_update_function = CACHE_UPDATE_FUNCTIONS.get(
        user_data.streaming_provider.service
        if user_data.streaming_provider
        else "torrent"
    )
    if cache_update_function:
        if asyncio.iscoroutinefunction(cache_update_function):
            await cache_update_function(filtered_streams, user_data)
        else:
            await asyncio.to_thread(cache_update_function, filtered_streams, user_data)

    # Step 3: Dynamically sort streams based on user preferences
    def dynamic_sort_key(stream):
        # Compute sort key values only once per stream
        sort_key_values = {
            sort_key: (
                getattr(stream, sort_key)
                if getattr(stream, sort_key) is not None
                else 0
            )
            for sort_key in user_data.torrent_sorting_priority
        }

        # Create the sort tuple, using resolution ranking for resolution sorting
        return tuple(
            const.RESOLUTION_RANKING[stream.resolution]
            if sort_key == "resolution"
            else sort_key_values[sort_key]
            for sort_key in user_data.torrent_sorting_priority
        )

    dynamically_sorted_streams = sorted(
        filtered_streams, key=dynamic_sort_key, reverse=True
    )

    # Step 4: Limit streams per resolution based on user preference, after dynamic sorting
    limited_streams = []
    streams_count_per_resolution = {}
    for stream in dynamically_sorted_streams:
        count = streams_count_per_resolution.get(stream.resolution, 0)
        if count < user_data.max_streams_per_resolution:
            limited_streams.append(stream)
            streams_count_per_resolution[stream.resolution] = count + 1

    return limited_streams


async def parse_stream_data(
    streams: list[TorrentStreams],
    user_data: UserData,
    secret_str: str,
    season: int = None,
    episode: int = None,
) -> list[Stream]:
    stream_list = []
    streams = await filter_and_sort_streams(streams, user_data)

    # Compute values that do not change per iteration outside the loop
    show_full_torrent_name = user_data.show_full_torrent_name
    streaming_provider_name = (
        user_data.streaming_provider.service.title()
        if user_data.streaming_provider
        else "Torrent"
    )
    has_streaming_provider = user_data.streaming_provider is not None
    base_proxy_url_template = (
        f"{settings.host_url}/streaming_provider/{secret_str}/stream?info_hash={{}}"
        if has_streaming_provider
        else None
    )

    for stream_data in streams:
        episode_data = stream_data.get_episode(season, episode)

        if show_full_torrent_name:
            torrent_name = (
                f"{stream_data.torrent_name}/{episode_data.title}"
                if episode_data
                else stream_data.torrent_name
            )
            torrent_name = "📂 " + torrent_name.replace(".torrent", "").replace(".", " ")
        else:
            torrent_name = None

        quality_detail_parts = [
            ("📺 " + stream_data.quality) if stream_data.quality else None,
            ("🎞️ " + stream_data.codec) if stream_data.codec else None,
            ("🎵 " + stream_data.audio) if stream_data.audio else None,
        ]
        quality_detail = " ".join(filter(None, quality_detail_parts))

        resolution = stream_data.resolution.upper() if stream_data.resolution else "N/A"
        streaming_provider_status = "⚡️" if stream_data.cached else "⏳"

        seeders_info = (
            f"👤 {stream_data.seeders}" if stream_data.seeders is not None else None
        )
        if episode_data and episode_data.size:
            size_info = f"{convert_bytes_to_readable(episode_data.size)} / {convert_bytes_to_readable(stream_data.size)}"
        else:
            size_info = convert_bytes_to_readable(stream_data.size)

        languages = (
            "🌐 " + " + ".join(stream_data.languages) if stream_data.languages else None
        )
        source_info = f"🔗 {stream_data.source}"

        primary_info = torrent_name if show_full_torrent_name else quality_detail
        secondary_info = " ".join(filter(None, [size_info, seeders_info]))

        description_parts = [
            primary_info,
            secondary_info,
            languages,
            source_info,
        ]
        description = "\n".join(filter(None, description_parts))

        stream_details = {
            "name": f"MediaFusion {streaming_provider_name} {resolution} {streaming_provider_status}",
            "description": description,
            "infoHash": stream_data.id,
            "fileIdx": episode_data.file_index
            if episode_data
            else stream_data.file_index,
            "behaviorHints": {
                "bingeGroup": f"MediaFusion-{quality_detail}-{resolution}",
            },
        }

        if has_streaming_provider:
            base_proxy_url = base_proxy_url_template.format(stream_data.id) + (
                f"&season={season}&episode={episode}" if episode_data else ""
            )
            stream_details.update(
                {"url": base_proxy_url, "behaviorHints": {"notWebReady": True}}
            )
            stream_details.pop("infoHash", None)
            stream_details.pop("fileIdx", None)

        stream_list.append(Stream(**stream_details))

    return stream_list


def convert_bytes_to_readable(size_bytes: int) -> str:
    """
    Convert a size in bytes into a more human-readable format.
    """
    if not size_bytes:
        return ""
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
    base_catalog = catalog.split("_")[-1]

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


def get_imdb_data(video_id: str) -> tuple[str, str]:
    try:
        movie = ia.get_movie(video_id.removeprefix("tt"), info="main")
    except Exception:
        return "", ""
    return movie.get("title"), movie.get("year")


async def parse_tv_stream_data(tv_streams: list[TVStreams]) -> list[Stream]:
    stream_list = []
    for stream in tv_streams:
        if stream.behaviorHints and stream.behaviorHints.get("is_redirect", False):
            stream_link = await get_redirector_url(
                stream.url,
                stream.behaviorHints.get("proxyHeaders", {}).get("request", {}),
            )
            if stream_link is None:
                continue
            stream.url = stream_link
        elif settings.validate_m3u8_urls_liveness:
            is_working, _ = await validate_m3u8_url(stream.url, stream.behaviorHints)
            if not is_working:
                continue

        country_info = f"\n🌐 {stream.country}" if stream.country else ""

        stream_list.append(
            Stream(
                name="MediaFusion",
                description=f"📺 {stream.name}{country_info}\n🔗 {stream.source}",
                url=stream.url,
                ytId=stream.ytId,
                behaviorHints=stream.behaviorHints,
            )
        )

    if not stream_list:
        stream_list.append(
            Stream(
                name="MediaFusion",
                description="No streams live at the moment.",
                url=f"{settings.host_url}/static/exceptions/source_not_live.mp4",
                behaviorHints={"notWebReady": True},
            )
        )

    return stream_list


async def fetch_downloaded_info_hashes(user_data: UserData) -> list[str]:
    if fetch_downloaded_info_hashes_function := FETCH_DOWNLOADED_INFO_HASHES_FUNCTIONS.get(
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


async def generate_manifest(manifest: dict, user_data: UserData, redis: Redis) -> dict:
    from db.crud import get_genres

    resources = manifest.get("resources", [])

    # Ensure catalogs are enabled
    if user_data.enable_catalogs:
        # Reorder catalogs based on the user's selection order
        ordered_catalogs = []
        for catalog_id in user_data.selected_catalogs:
            for catalog in manifest.get("catalogs", []):
                if catalog["id"] == catalog_id:
                    if catalog_id == "live_tv":
                        # Add the available genres to the live TV catalog
                        catalog["extra"][1]["options"] = await get_genres(
                            catalog_type="tv", redis=redis
                        )
                    ordered_catalogs.append(catalog)
                    break

        manifest["catalogs"] = ordered_catalogs
    else:
        # If catalogs are not enabled, clear them from the manifest
        manifest["catalogs"] = []
        # Define a default stream resource if catalogs are disabled
        resources = [
            {
                "name": "stream",
                "types": ["movie", "series", "tv"],
                "idPrefixes": ["tt", "mf"],
            }
        ]

    # Adjust manifest details based on the selected streaming provider
    if user_data.streaming_provider:
        provider_name = user_data.streaming_provider.service.title()
        manifest["name"] += f" {provider_name}"
        manifest["id"] += f".{provider_name.lower()}"

        # Include watchlist catalogs if enabled
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
            # Prepend watchlist catalogs to the sorted user-selected catalogs
            manifest["catalogs"] = watchlist_catalogs + manifest["catalogs"]
            resources = manifest["resources"]

    # Ensure the resource list is updated accordingly
    manifest["resources"] = resources
    return manifest


def is_contain_18_plus_keywords(title: str) -> bool:
    """
    Check if the title contains 18+ keywords to filter out adult content.
    """
    return ADULT_CONTENT_KEYWORDS.search(title) is not None


def get_json_data(file_name: str) -> dict:
    with open(file_name) as file:
        return json.load(file)
