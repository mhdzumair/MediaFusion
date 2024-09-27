import asyncio
import functools
import logging
from typing import Optional, List

import math
import re

from thefuzz import fuzz

from db.config import settings
from db.models import TorrentStreams, TVStreams
from db.schemas import Stream, UserData
from streaming_providers import mapper
from utils import const
from utils.const import STREAMING_PROVIDERS_SHORT_NAMES
from utils.network import encode_mediaflow_proxy_url
from utils.runtime_const import ADULT_CONTENT_KEYWORDS, TRACKERS
from utils.validation_helper import validate_m3u8_or_mpd_url_with_cache


async def filter_and_sort_streams(
    streams: list[TorrentStreams], user_data: UserData, user_ip: str | None = None
) -> list[TorrentStreams]:
    # Convert to sets for faster lookups
    selected_catalogs_set = set(user_data.selected_catalogs)
    selected_resolutions_set = set(user_data.selected_resolutions)
    quality_filter_set = set(
        quality
        for group in user_data.quality_filter
        for quality in const.QUALITY_GROUPS[group]
    )
    language_filter_set = set(user_data.language_sorting)

    valid_resolutions = const.SUPPORTED_RESOLUTIONS
    valid_qualities = const.SUPPORTED_QUALITIES
    valid_languages = const.SUPPORTED_LANGUAGES

    # Step 1: Filter streams and add normalized attributes
    filtered_streams = []
    for stream in streams:
        # Add normalized attributes as dynamic properties
        stream.filtered_resolution = (
            stream.resolution if stream.resolution in valid_resolutions else None
        )
        stream.filtered_quality = (
            stream.quality if stream.quality in valid_qualities else None
        )
        stream.filtered_languages = [
            lang for lang in stream.languages if lang in valid_languages
        ] or [None]

        # Check if any of the stream's catalogs are in the selected catalogs
        if not any(catalog in selected_catalogs_set for catalog in stream.catalog):
            continue

        if stream.filtered_resolution not in selected_resolutions_set:
            continue

        if stream.size > user_data.max_size:
            continue

        if stream.filtered_quality not in quality_filter_set:
            continue

        if "language" in user_data.torrent_sorting_priority and not any(
            lang in language_filter_set for lang in stream.filtered_languages
        ):
            continue

        if is_contain_18_plus_keywords(stream.torrent_name):
            continue

        filtered_streams.append(stream)

    if not filtered_streams:
        return []

    # Step 2: Update cache status based on provider
    if user_data.streaming_provider:
        cache_update_function = mapper.CACHE_UPDATE_FUNCTIONS.get(
            user_data.streaming_provider.service
        )
        kwargs = dict(streams=filtered_streams, user_data=user_data, user_ip=user_ip)
        if cache_update_function:
            try:
                if asyncio.iscoroutinefunction(cache_update_function):
                    await cache_update_function(**kwargs)
                else:
                    await asyncio.to_thread(cache_update_function, **kwargs)
            except Exception as error:
                logging.exception(
                    f"Failed to update cache status for {user_data.streaming_provider.service}: {error}"
                )

    # Step 3: Dynamically sort streams based on user preferences
    def dynamic_sort_key(stream):
        return tuple(
            (
                const.RESOLUTION_RANKING.get(stream.filtered_resolution, 0)
                if key == "resolution"
                else (
                    -min(
                        (
                            user_data.language_sorting.index(lang)
                            for lang in stream.filtered_languages
                            if lang in language_filter_set
                        ),
                        default=len(user_data.language_sorting),
                    )
                    if key == "language"
                    else (
                        const.QUALITY_RANKING.get(stream.filtered_quality, 0)
                        if key == "quality"
                        else (
                            getattr(stream, key, 0)
                            if key in stream.model_fields_set
                            else 0
                        )
                    )
                )
            )
            for key in user_data.torrent_sorting_priority
        )

    dynamically_sorted_streams = sorted(
        filtered_streams, key=dynamic_sort_key, reverse=True
    )

    # Step 4: Limit streams per resolution based on user preference, after dynamic sorting
    limited_streams = []
    streams_count_per_resolution = {}
    for stream in dynamically_sorted_streams:
        count = streams_count_per_resolution.get(stream.filtered_resolution, 0)
        if count < user_data.max_streams_per_resolution:
            limited_streams.append(stream)
            streams_count_per_resolution[stream.filtered_resolution] = count + 1

    return limited_streams


async def parse_stream_data(
    streams: list[TorrentStreams],
    user_data: UserData,
    secret_str: str,
    season: int = None,
    episode: int = None,
    user_ip: str | None = None,
    is_series: bool = False,
) -> list[Stream]:
    if not streams:
        return []

    streams = await filter_and_sort_streams(streams, user_data, user_ip)

    # Precompute constant values
    show_full_torrent_name = user_data.show_full_torrent_name
    streaming_provider_name = (
        STREAMING_PROVIDERS_SHORT_NAMES.get(user_data.streaming_provider.service, "P2P")
        if user_data.streaming_provider
        else "P2P"
    )
    has_streaming_provider = user_data.streaming_provider is not None
    download_via_browser = (
        has_streaming_provider
        and user_data.streaming_provider.download_via_browser
        and not settings.disable_download_via_browser
    )

    base_proxy_url_template = ""
    if has_streaming_provider:
        if (
            user_data.mediaflow_config
            and user_data.mediaflow_config.proxy_debrid_streams
        ):
            streaming_provider_name += " 🕵🏼‍♂️"

        base_proxy_url_template = (
            f"{settings.host_url}/streaming_provider/{secret_str}/stream?info_hash={{}}"
        )

    stream_list = []
    for stream_data in streams:
        episode_data = stream_data.get_episode(season, episode) if is_series else None
        if is_series and not episode_data:
            continue

        if episode_data:
            file_name = episode_data.filename
            file_index = episode_data.file_index
        else:
            file_name = stream_data.filename
            file_index = stream_data.file_index

        if show_full_torrent_name:
            torrent_name = (
                f"{stream_data.torrent_name}/{episode_data.title or episode_data.filename or ''}"
                if episode_data
                else stream_data.torrent_name
            )
            torrent_name = "📂 " + torrent_name.replace(".torrent", "").replace(
                ".", " "
            )
        else:
            torrent_name = None

        # Compute quality_detail
        quality_detail = " ".join(
            filter(
                None,
                [
                    f"📺 {stream_data.quality}" if stream_data.quality else None,
                    f"🎞️ {stream_data.codec}" if stream_data.codec else None,
                    f"🎵 {stream_data.audio}" if stream_data.audio else None,
                ],
            )
        )

        resolution = stream_data.resolution.upper() if stream_data.resolution else "N/A"
        streaming_provider_status = "⚡️" if stream_data.cached else "⏳"
        seeders_info = (
            f"👤 {stream_data.seeders}" if stream_data.seeders is not None else None
        )
        if episode_data and episode_data.size:
            file_size = episode_data.size
            size_info = f"{convert_bytes_to_readable(file_size)} / {convert_bytes_to_readable(stream_data.size)}"
        else:
            file_size = stream_data.size
            size_info = convert_bytes_to_readable(file_size)

        languages = (
            f"🌐 {' + '.join(stream_data.languages)}" if stream_data.languages else None
        )
        source_info = f"🔗 {stream_data.source}"

        description = "\n".join(
            filter(
                None,
                [
                    torrent_name if show_full_torrent_name else quality_detail,
                    " ".join(filter(None, [size_info, seeders_info])),
                    languages,
                    source_info,
                ],
            )
        )

        stream_details = {
            "name": f"{settings.addon_name} {streaming_provider_name} {resolution} {streaming_provider_status}",
            "description": description,
            "behaviorHints": {
                "bingeGroup": f"{settings.addon_name.replace(' ', '-')}-{quality_detail}-{resolution}",
                "filename": file_name or stream_data.torrent_name,
                "videoSize": file_size,
            },
        }

        if has_streaming_provider:
            stream_details["url"] = base_proxy_url_template.format(stream_data.id) + (
                f"&season={season}&episode={episode}" if episode_data else ""
            )
            stream_details["behaviorHints"]["notWebReady"] = True
        else:
            stream_details["infoHash"] = stream_data.id
            stream_details["fileIdx"] = file_index
            stream_details["sources"] = [
                f"tracker:{tracker}"
                for tracker in (stream_data.announce_list or TRACKERS)
            ] + [f"dht:{stream_data.id}"]

        stream_list.append(Stream(**stream_details))

    if stream_list and download_via_browser:
        download_url = f"{settings.host_url}/download/{secret_str}/{'series' if is_series else 'movie'}/{streams[0].meta_id}"
        if is_series:
            download_url += f"/{season}/{episode}"
        stream_list.append(
            Stream(
                name=f"{settings.addon_name} {streaming_provider_name} 📥",
                description="📥 Download Torrent Streams via WebBrowser",
                externalUrl=download_url,
            )
        )

    return stream_list


@functools.lru_cache(maxsize=1024)
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


@functools.lru_cache(maxsize=1024)
def convert_size_to_bytes(size_str: str) -> int:
    """Convert size string to bytes."""
    match = re.match(r"(\d+(?:\.\d+)?)\s*(GB|MB|KB|B)", size_str, re.IGNORECASE)
    if match:
        size, unit = match.groups()
        size = float(size)
        match unit.lower():
            case "gb":
                return int(size * 1024**3)
            case "mb":
                return int(size * 1024**2)
            case "kb":
                return int(size * 1024)
            case "b":
                return int(size)
    return 0


async def parse_tv_stream_data(
    tv_streams: List[TVStreams], user_data: UserData
) -> List[Stream]:
    is_mediaflow_proxy_enabled = (
        user_data.mediaflow_config and user_data.mediaflow_config.proxy_live_streams
    )
    addon_name = (
        f"{settings.addon_name} {'🕵🏼‍♂️' if is_mediaflow_proxy_enabled else '📡'}"
    )

    stream_processor = functools.partial(
        process_stream,
        is_mediaflow_proxy_enabled=is_mediaflow_proxy_enabled,
        mediaflow_config=user_data.mediaflow_config,
        addon_name=addon_name,
    )

    processed_streams = await asyncio.gather(
        *[stream_processor(stream) for stream in reversed(tv_streams)]
    )

    stream_list = []
    is_mediaflow_needed = False

    for result in processed_streams:
        if result:
            if isinstance(result, Stream):
                stream_list.append(result)
            elif result == "MEDIAFLOW_NEEDED":
                is_mediaflow_needed = True

    if not stream_list:
        if is_mediaflow_needed:
            stream_list.append(
                create_exception_stream(
                    addon_name,
                    "🚫 MediaFlow Proxy is required to watch this stream.",
                    "mediaflow_proxy_required.mp4",
                )
            )
        else:
            stream_list.append(
                create_exception_stream(
                    addon_name,
                    "🚫 No streams are live at the moment.",
                    "no_streams_live.mp4",
                )
            )

    return stream_list


async def process_stream(
    stream: TVStreams,
    is_mediaflow_proxy_enabled: bool,
    mediaflow_config,
    addon_name: str,
) -> Optional[Stream | str]:
    if settings.validate_m3u8_urls_liveness:
        is_working = await validate_m3u8_or_mpd_url_with_cache(
            stream.url, stream.behaviorHints or {}
        )
        if not is_working:
            return None

    stream_url, behavior_hints = stream.url, stream.behaviorHints
    behavior_hints = behavior_hints if behavior_hints else {}

    if stream.drm_key or "dlhd" in stream.source:
        if not is_mediaflow_proxy_enabled:
            return "MEDIAFLOW_NEEDED"
        stream_url = get_proxy_url(stream, mediaflow_config)
        behavior_hints["proxyHeaders"] = None
    elif is_mediaflow_proxy_enabled:
        stream_url = get_proxy_url(stream, mediaflow_config)
        behavior_hints["proxyHeaders"] = None

    country_info = f"\n🌐 {stream.country}" if stream.country else ""

    return Stream(
        name=addon_name,
        description=f"📺 {stream.name}{country_info}\n🔗 {stream.source}",
        url=stream_url,
        ytId=stream.ytId,
        behaviorHints=behavior_hints,
    )


def get_proxy_url(stream: TVStreams, mediaflow_config) -> str:
    endpoint = (
        "/proxy/mpd/manifest.m3u8" if stream.drm_key else "/proxy/hls/manifest.m3u8"
    )
    query_params = {}
    if stream.drm_key:
        query_params = {"key_id": stream.drm_key_id, "key": stream.drm_key}
    elif "dlhd" in stream.source:
        query_params = {"use_request_proxy": False}

    return encode_mediaflow_proxy_url(
        mediaflow_config.proxy_url,
        endpoint,
        stream.url,
        query_params=query_params,
        request_headers=stream.behaviorHints.get("proxyHeaders", {}).get("request", {}),
        encryption_api_password=mediaflow_config.api_password,
    )


def create_exception_stream(
    addon_name: str, description: str, exc_file_name: str
) -> Stream:
    return Stream(
        name=addon_name,
        description=description,
        url=f"{settings.host_url}/static/exceptions/{exc_file_name}",
        behaviorHints={"notWebReady": True},
    )


async def fetch_downloaded_info_hashes(
    user_data: UserData, user_ip: str | None
) -> list[str]:
    kwargs = dict(user_data=user_data, user_ip=user_ip)
    if fetch_downloaded_info_hashes_function := mapper.FETCH_DOWNLOADED_INFO_HASHES_FUNCTIONS.get(
        user_data.streaming_provider.service
    ):
        try:
            if asyncio.iscoroutinefunction(fetch_downloaded_info_hashes_function):
                downloaded_info_hashes = await fetch_downloaded_info_hashes_function(
                    **kwargs
                )
            else:
                downloaded_info_hashes = await asyncio.to_thread(
                    fetch_downloaded_info_hashes_function, **kwargs
                )

            return downloaded_info_hashes
        except Exception as error:
            logging.error(
                f"Failed to fetch downloaded info hashes for {user_data.streaming_provider.service}: {error}"
            )
            pass

    return []


async def generate_manifest(manifest: dict, user_data: UserData) -> dict:
    from db.crud import get_genres

    resources = manifest.get("resources", [])
    manifest["name"] = settings.addon_name
    manifest["id"] += f".{settings.addon_name.lower().replace(' ', '')}"
    manifest["logo"] = settings.logo_url

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
                            catalog_type="tv"
                        )
                    ordered_catalogs.append(catalog)
                    break

        manifest["catalogs"] = ordered_catalogs
        if not user_data.enable_imdb_metadata:
            # Remove IMDb metadata if disabled
            resources[-1]["idPrefixes"].remove("tt")
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


@functools.lru_cache(maxsize=1024)
def is_contain_18_plus_keywords(title: str) -> bool:
    """
    Check if the title contains 18+ keywords to filter out adult content.
    """
    return ADULT_CONTENT_KEYWORDS.search(title) is not None


def calculate_max_similarity_ratio(
    torrent_title: str, title: str, aka_titles: list[str] | None = None
) -> int:
    # Check similarity with the main title
    title_similarity_ratio = fuzz.ratio(torrent_title.lower(), title.lower())

    # Check similarity with aka titles
    aka_similarity_ratios = (
        [
            fuzz.ratio(torrent_title.lower(), aka_title.lower())
            for aka_title in aka_titles
        ]
        if aka_titles
        else []
    )

    # Use the maximum similarity ratio
    max_similarity_ratio = max([title_similarity_ratio] + aka_similarity_ratios)

    return max_similarity_ratio
