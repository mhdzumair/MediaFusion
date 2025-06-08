import asyncio
import functools
import json
import logging
import math
import re
from datetime import datetime, timezone
from os.path import basename
from typing import Optional, List, Any
from urllib.parse import quote, urlparse

from thefuzz import fuzz

from db.config import settings
from db.enums import TorrentType
from db.models import TorrentStreams, TVStreams
from db.schemas import Stream, UserData, SortingOption
from streaming_providers import mapper
from streaming_providers.cache_helpers import (
    get_cached_status,
    store_cached_info_hashes,
)
from utils import const
from utils.config import config_manager
from utils.const import STREAMING_PROVIDERS_SHORT_NAMES, CERTIFICATION_MAPPING
from utils.network import encode_mediaflow_proxy_url
from utils.runtime_const import TRACKERS, MANIFEST_TEMPLATE, ADULT_PARSER
from utils.validation_helper import validate_m3u8_or_mpd_url_with_cache


async def filter_and_sort_streams(
    streams: list[TorrentStreams],
    user_data: UserData,
    stremio_video_id: str,
    user_ip: str | None = None,
) -> tuple[list[TorrentStreams], dict]:
    # Convert to sets for faster lookups
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
    filtered_reasons = {
        "Requires Streaming Provider": 0,
        "Requires Private Tracker Support": 0,
        "Resolution Not Selected": 0,
        "Size Limit Exceeded": 0,
        "Quality Not Selected": 0,
        "Language Not Selected": 0,
        "Strict 18+ Keyword Filter": 0,
        "No Cached Streams": 0,
    }

    for stream in streams:
        # Skip private torrents if streaming provider is not supported
        if stream.torrent_type != TorrentType.PUBLIC:
            if not user_data.streaming_provider:
                filtered_reasons["Requires Streaming Provider"] += 1
                continue
            if (
                stream.torrent_type != TorrentType.WEB_SEED
                and user_data.streaming_provider.service
                not in const.SUPPORTED_PRIVATE_TRACKER_STREAMING_PROVIDERS
            ):
                filtered_reasons["Requires Private Tracker Support"] += 1
                continue
        # Create a copy of the stream model to avoid modifying the original
        stream = stream.model_copy()
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
        stream.cached = False

        if stream.filtered_resolution not in selected_resolutions_set:
            filtered_reasons["Resolution Not Selected"] += 1
            continue

        if stream.size > user_data.max_size:
            filtered_reasons["Size Limit Exceeded"] += 1
            continue

        if stream.filtered_quality not in quality_filter_set:
            filtered_reasons["Quality Not Selected"] += 1
            continue

        if not any(lang in language_filter_set for lang in stream.filtered_languages):
            filtered_reasons["Language Not Selected"] += 1
            continue

        if is_contain_18_plus_keywords(stream.torrent_name):
            filtered_reasons["Strict 18+ Keyword Filter"] += 1
            continue

        filtered_streams.append(stream)

    if not filtered_streams:
        return filtered_streams, filtered_reasons

    # Step 2: Update cache status based on provider
    if user_data.streaming_provider:
        service = user_data.streaming_provider.service
        info_hashes = [stream.id for stream in filtered_streams]

        # First check Redis cache
        cached_statuses = await get_cached_status(
            user_data.streaming_provider, info_hashes
        )

        # Update streams with cached status from Redis
        uncached_streams = []
        for stream in filtered_streams:
            if cached_statuses.get(stream.id, False):
                stream.cached = True
            else:
                stream.cached = False
                uncached_streams.append(stream)

        # For streams not found in Redis cache, use provider's cache check
        if uncached_streams:
            cache_update_function = mapper.CACHE_UPDATE_FUNCTIONS.get(service)
            if cache_update_function:
                try:
                    service_name = await cache_update_function(
                        streams=uncached_streams,
                        user_data=user_data,
                        user_ip=user_ip,
                        stremio_video_id=stremio_video_id,
                    )
                    # Store only the cached ones in Redis
                    cached_info_hashes = [
                        stream.id for stream in uncached_streams if stream.cached
                    ]
                    if cached_info_hashes:
                        await store_cached_info_hashes(
                            user_data.streaming_provider,
                            cached_info_hashes,
                            service_name,
                        )
                except Exception as error:
                    logging.exception(
                        f"Failed to update cache status for {service}: {error}"
                    )

        if user_data.streaming_provider.only_show_cached_streams:
            cached_filtered_streams = [
                stream for stream in filtered_streams if stream.cached
            ]
            if not cached_filtered_streams:
                filtered_reasons["No Cached Streams"] = len(filtered_streams)
                return filtered_streams, filtered_reasons
            filtered_streams = cached_filtered_streams

    # Step 3: Dynamically sort streams based on user preferences
    def dynamic_sort_key(torrent_stream: TorrentStreams) -> tuple:
        def key_value(sorting_option: SortingOption) -> Any:
            key = sorting_option.key
            multiplier = 1 if sorting_option.direction == "asc" else -1

            match key:
                case "cached":
                    return multiplier * (1 if torrent_stream.cached else 0)
                case "resolution":
                    return multiplier * const.RESOLUTION_RANKING.get(
                        torrent_stream.filtered_resolution, 0
                    )
                case "quality":
                    return multiplier * const.QUALITY_RANKING.get(
                        torrent_stream.filtered_quality, 0
                    )
                case "size":
                    return multiplier * torrent_stream.size
                case "seeders":
                    return multiplier * (torrent_stream.seeders or 0)
                case "created_at":
                    created_at = torrent_stream.created_at
                    if isinstance(created_at, datetime):
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)
                        return multiplier * created_at.timestamp()
                    elif isinstance(created_at, (int, float)):
                        return multiplier * created_at
                    else:
                        return (
                            multiplier
                            * datetime.min.replace(tzinfo=timezone.utc).timestamp()
                        )
                case "language":
                    return multiplier * -min(
                        (
                            user_data.language_sorting.index(lang)
                            for lang in torrent_stream.filtered_languages
                            if lang in language_filter_set
                        ),
                        default=len(user_data.language_sorting),
                    )
                case _ if key in torrent_stream.model_fields_set:
                    value = getattr(torrent_stream, key, 0)
                    return multiplier * (value if value is not None else 0)
                case _:
                    return 0

        return tuple(key_value(option) for option in user_data.torrent_sorting_priority)

    try:
        # Sort streams based on the dynamic key
        dynamically_sorted_streams = sorted(filtered_streams, key=dynamic_sort_key)

    except Exception:
        logging.exception(
            f"torrent_sorting_priority: {user_data.torrent_sorting_priority}: sort data: {[dynamic_sort_key(stream) for stream in filtered_streams]}"
        )
        dynamically_sorted_streams = filtered_streams

    # Step 4: Limit streams per resolution based on user preference, after dynamic sorting
    limited_streams = []
    streams_count_per_resolution = {}
    for stream in dynamically_sorted_streams:
        count = streams_count_per_resolution.get(stream.filtered_resolution, 0)
        if count < user_data.max_streams_per_resolution:
            limited_streams.append(stream)
            streams_count_per_resolution[stream.filtered_resolution] = count + 1

    return limited_streams, filtered_reasons


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

    streaming_provider_name = (
        STREAMING_PROVIDERS_SHORT_NAMES.get(user_data.streaming_provider.service, "P2P")
        if user_data.streaming_provider
        else "P2P"
    )
    addon_name = f"{settings.addon_name} {streaming_provider_name}"

    stremio_video_id = (
        f"{streams[0].meta_id}:{season}:{episode}" if is_series else streams[0].meta_id
    )
    streams, filtered_reasons = await filter_and_sort_streams(
        streams, user_data, stremio_video_id, user_ip
    )

    if not streams:
        reason_data = [
            f"{count} x {reason}"
            for reason, count in filtered_reasons.items()
            if count > 0
        ]
        reasons = "\n".join(reason_data)
        return [
            create_exception_stream(
                settings.addon_name,
                f"🚫 Streams Found\n⚙️ Filtered by your configuration preferences\n{reasons}",
                "filtered_no_streams.mp4",
            )
        ]

    # Precompute constant values
    show_full_torrent_name = user_data.show_full_torrent_name
    has_streaming_provider = user_data.streaming_provider is not None
    download_via_browser = (
        has_streaming_provider and user_data.streaming_provider.download_via_browser
    )

    base_proxy_url_template = ""
    if has_streaming_provider:
        if (
            user_data.mediaflow_config
            and user_data.mediaflow_config.proxy_debrid_streams
        ):
            addon_name += " 🕵🏼‍♂️"

        base_proxy_url_template = (
            f"{settings.host_url}/streaming_provider/{secret_str}/playback/{{}}"
        )

    stream_list = []
    for stream_data in streams:
        episode_variants = (
            stream_data.get_episodes(season, episode) if is_series else [None]
        )
        if is_series and not episode_variants:
            continue

        for episode_data in episode_variants:
            if episode_data:
                file_name = episode_data.filename
                file_index = episode_data.file_index
            else:
                file_name = stream_data.filename
                file_index = stream_data.file_index

            # make sure file_name is basename
            file_name = basename(file_name) if file_name else None

            if show_full_torrent_name:
                torrent_name = (
                    f"{stream_data.torrent_name} ┈➤ {episode_data.filename}"
                    if episode_data and episode_data.filename
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
                        f"🎨 {'|'.join(stream_data.hdr)}" if stream_data.hdr else None,
                        f"📺 {stream_data.quality}" if stream_data.quality else None,
                        f"🎞️ {stream_data.codec}" if stream_data.codec else None,
                        (
                            f"🎵 {'|'.join(stream_data.audio)}"
                            if stream_data.audio
                            else None
                        ),
                    ],
                )
            )

            resolution = (
                stream_data.resolution.upper() if stream_data.resolution else "N/A"
            )
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

            if user_data.show_language_country_flag:
                languages = filter(
                    None,
                    set(
                        [
                            const.LANGUAGE_COUNTRY_FLAGS.get(lang)
                            for lang in stream_data.languages
                        ]
                    ),
                )
            else:
                languages = stream_data.languages

            languages = f"🌐 {' + '.join(languages)}" if stream_data.languages else None
            source_info = f"🔗 {stream_data.source}"
            if stream_data.uploader:
                source_info += f" 🧑‍💻 {stream_data.uploader}"

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
                "name": f"{addon_name} {resolution} {streaming_provider_status}",
                "description": description,
                "behaviorHints": {
                    "bingeGroup": f"{settings.addon_name.replace(' ', '-')}-{quality_detail}-{resolution}",
                    "filename": file_name or stream_data.torrent_name,
                    "videoSize": file_size,
                },
            }

            if has_streaming_provider:
                stream_details["url"] = base_proxy_url_template.format(stream_data.id)
                if episode_data:
                    stream_details["url"] += f"/{season}/{episode}"
                if file_name:
                    stream_details["url"] += f"/{quote(file_name)}"
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

    if stream.drm_key:
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

def get_dlhd_channel_url(stream: TVStreams) -> str:
    parsed_url = urlparse(stream.url)
    channel_id = parsed_url.path.split("/")[-2]
    channel_number = re.search(r"(\d+)", channel_id).group(1)
    channel_url = config_manager.get_scraper_config("dlhd", "channel_url")
    return channel_url.format(channel_number=channel_number)


def get_proxy_url(stream: TVStreams, mediaflow_config) -> str:
    endpoint = (
        "/proxy/mpd/manifest.m3u8" if stream.drm_key else "/proxy/hls/manifest.m3u8"
    )
    query_params = {}
    if stream.drm_key:
        query_params = {"key_id": stream.drm_key_id, "key": stream.drm_key}
    elif stream.source == "DaddyLiveHD":
        query_params = {
            "use_request_proxy": False,
            "host": "DLHD",
            "redirect_stream": True,
        }
        stream.url = get_dlhd_channel_url(stream)
        endpoint = "/extractor/video"
        stream.behaviorHints = {"proxyHeaders": {}}

    return encode_mediaflow_proxy_url(
        mediaflow_config.proxy_url,
        endpoint,
        stream.url,
        query_params=query_params,
        request_headers=stream.behaviorHints.get("proxyHeaders", {}).get("request", {}),
        response_headers=stream.behaviorHints.get("proxyHeaders", {}).get(
            "response", {}
        ),
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
            downloaded_info_hashes = await fetch_downloaded_info_hashes_function(
                **kwargs
            )
            return downloaded_info_hashes
        except Exception as error:
            logging.exception(
                f"Failed to fetch downloaded info hashes for {user_data.streaming_provider.service}: {error}"
            )
            pass

    return []


async def generate_manifest(user_data: UserData, genres: dict) -> dict:
    streaming_provider_name = None
    streaming_provider_short_name = None
    enable_watchlist_catalogs = False
    addon_name = settings.addon_name

    if user_data.streaming_provider:
        streaming_provider_name = user_data.streaming_provider.service
        streaming_provider_short_name = STREAMING_PROVIDERS_SHORT_NAMES.get(
            user_data.streaming_provider.service
        )
        enable_watchlist_catalogs = (
            user_data.streaming_provider.enable_watchlist_catalogs
        )
        addon_name += f" {streaming_provider_short_name}"

    if user_data.mediaflow_config:
        addon_name += " 🕵🏼‍♂️"

    mdblist_data = {}
    if user_data.mdblist_config:
        mdblist_data = {
            f"mdblist_{mdblist.catalog_type}_{mdblist.id}": mdblist.model_dump(
                include={"title", "catalog_type"}
            )
            for mdblist in user_data.mdblist_config.lists
        }

    manifest_data = {
        "addon_name": addon_name,
        "version": settings.version,
        "contact_email": settings.contact_email,
        "description": settings.description,
        "logo_url": settings.logo_url,
        "streaming_provider_name": streaming_provider_name,
        "streaming_provider_short_name": streaming_provider_short_name,
        "enable_imdb_metadata": user_data.enable_imdb_metadata,
        "enable_catalogs": user_data.enable_catalogs,
        "enable_watchlist_catalogs": enable_watchlist_catalogs,
        "selected_catalogs": user_data.selected_catalogs,
        "genres": genres,
        "mdblist_data": mdblist_data,
    }

    manifest_json = MANIFEST_TEMPLATE.render(manifest_data)
    try:
        return json.loads(manifest_json)
    except json.JSONDecodeError as e:
        logging.exception(f"Failed to parse manifest JSON: {e}")
        return {}


@functools.lru_cache(maxsize=1024)
def is_contain_18_plus_keywords(title: str) -> bool:
    """
    Check if the title contains 18+ keywords to filter out adult content.
    """
    if not settings.adult_content_filter_in_torrent_title:
        return False

    return ADULT_PARSER.parse(title).get("adult", False)


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


def get_certification_level(certificates: list) -> str:
    """
    Get the highest certification level from a list of certificates.
    Returns the category name (All Ages, Children, etc.) based on the highest restriction level.
    """
    if not certificates:
        return "Unknown"

    # Order of restriction levels from lowest to highest
    levels = ["All Ages", "Children", "Parental Guidance", "Teens", "Adults", "Adults+"]

    highest_level = "Unknown"
    for certificate in certificates:
        for level in levels:
            if certificate in CERTIFICATION_MAPPING[level]:
                # If current level is more restrictive, update highest_level
                if (
                    levels.index(level) > levels.index(highest_level)
                    if highest_level in levels
                    else -1
                ):
                    highest_level = level

    return highest_level


def get_age_rating_emoji(certification_level: str) -> str:
    """
    Get appropriate emoji for certification level
    """
    emoji_mapping = {
        "All Ages": "👨‍👩‍👧‍👦",
        "Children": "👶",
        "Parental Guidance": "👨‍👩‍👧‍👦",
        "Teens": "👱",
        "Adults": "🔞",
        "Adults+": "🔞",
        "Unknown": "❓",
    }
    return emoji_mapping.get(certification_level, "❓")


def get_nudity_status_emoji(nudity_status: str) -> str:
    """
    Get appropriate emoji for nudity status
    """
    emoji_mapping = {
        "None": "👕",
        "Mild": "⚠️",
        "Moderate": "🔞",
        "Severe": "⛔",
    }
    return emoji_mapping.get(nudity_status, "❓")


def create_content_warning_message(metadata) -> str:
    """
    Create a formatted warning message with emojis based on movie metadata
    """
    cert_level = get_certification_level(metadata.parent_guide_certificates)
    cert_emoji = get_age_rating_emoji(cert_level)
    nudity_emoji = get_nudity_status_emoji(metadata.parent_guide_nudity_status)

    message = (
        f"⚠️ Content Warning ⚠️\n"
        f"This content may not be suitable for your preferences:\n"
        f"Certification: {cert_emoji} {cert_level}\n"
        f"Nudity Status: {nudity_emoji} {metadata.parent_guide_nudity_status}"
    )

    if "Adult" in metadata.genres:
        message += "\n🔞 Genre: Adult (Strict 18+ Filter Applied)"

    return message
