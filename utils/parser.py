import asyncio
import functools
import json
import logging
import math
import re
from datetime import UTC, datetime
from os.path import basename
from typing import Any, Optional, Union
from urllib.parse import quote, urlparse

from thefuzz import fuzz

from db.config import settings
from db.enums import TorrentType
from db.schemas import (
    HTTPStreamData,
    RichStream,
    RichStreamMetadata,
    SortingOption,
    Stream,
    StreamBehaviorHints,
    StreamingProvider,
    StreamTemplate,
    TelegramStreamData,
    TorrentStreamData,
    TVStreams,
    UsenetStreamData,
    UserData,
    YouTubeStreamData,
)
from streaming_providers import mapper
from streaming_providers.exceptions import ProviderException
from streaming_providers.cache_helpers import (
    get_cached_status,
    is_cache_check_done,
    mark_cache_check_done,
    store_cached_info_hashes,
)
from utils import const
from utils.config import config_manager
from utils.nzb_storage import generate_signed_nzb_url
from utils.const import CERTIFICATION_MAPPING, STREAMING_PROVIDERS_SHORT_NAMES
from utils.network import encode_mediaflow_proxy_url
from utils.runtime_const import ADULT_PARSER, MANIFEST_TEMPLATE, TRACKERS
from utils.template_engine import render_template as engine_render_template
from utils.validation_helper import validate_m3u8_or_mpd_url_with_cache

# Union type for all stream data types that go through parse_stream_data
AnyStreamData = Union[TorrentStreamData, UsenetStreamData, TelegramStreamData, HTTPStreamData, YouTubeStreamData]


async def filter_and_sort_streams(
    streams: list[AnyStreamData],
    user_data: UserData,
    stremio_video_id: str,
    user_ip: str | None = None,
    provider_override: StreamingProvider | None = None,
) -> tuple[list[AnyStreamData], dict]:
    # Convert to sets for faster lookups
    selected_resolutions_set = set(user_data.selected_resolutions)
    quality_filter_set = set(quality for group in user_data.quality_filter for quality in const.QUALITY_GROUPS[group])
    language_filter_set = set(user_data.language_sorting)

    valid_resolutions = const.SUPPORTED_RESOLUTIONS
    valid_qualities = const.SUPPORTED_QUALITIES
    valid_languages = const.SUPPORTED_LANGUAGES

    # Pre-compile stream name filter patterns
    stream_name_filter_mode = user_data.stream_name_filter_mode
    compiled_name_patterns: list[re.Pattern] | list[str] = []
    if stream_name_filter_mode != "disabled" and user_data.stream_name_filter_patterns:
        if user_data.stream_name_filter_use_regex:
            for pattern in user_data.stream_name_filter_patterns:
                try:
                    compiled_name_patterns.append(re.compile(pattern, re.IGNORECASE))
                except re.error:
                    logging.warning(f"Invalid regex pattern ignored: {pattern}")
        else:
            compiled_name_patterns = [p.lower() for p in user_data.stream_name_filter_patterns]

    # Step 1: Filter streams and add normalized attributes
    filtered_streams = []
    filtered_reasons = {
        "Requires Streaming Provider": 0,
        "Requires Private Tracker Support": 0,
        "Resolution Not Selected": 0,
        "Max Size Exceeded": 0,
        "Min Size Not Met": 0,
        "Quality Not Selected": 0,
        "Language Not Selected": 0,
        "Strict 18+ Keyword Filter": 0,
        "Stream Name Filter": 0,
        "No Cached Streams": 0,
    }

    # Use provider_override if given; otherwise fall back to primary provider
    primary_provider = provider_override or user_data.get_primary_provider()
    for stream in streams:
        # Skip private torrents if streaming provider is not supported
        # Non-torrent streams (usenet, telegram) don't have torrent_type; treat as public
        torrent_type = getattr(stream, "torrent_type", TorrentType.PUBLIC)
        if torrent_type != TorrentType.PUBLIC:
            if not primary_provider:
                filtered_reasons["Requires Streaming Provider"] += 1
                continue
            if (
                torrent_type != TorrentType.WEB_SEED
                and primary_provider.service not in const.SUPPORTED_PRIVATE_TRACKER_STREAMING_PROVIDERS
            ):
                filtered_reasons["Requires Private Tracker Support"] += 1
                continue
        # Create a copy of the stream model to avoid modifying the original
        stream = stream.model_copy()
        # Add normalized attributes as dynamic properties
        stream.filtered_resolution = (
            stream.resolution if getattr(stream, "resolution", None) in valid_resolutions else None
        )
        stream.filtered_quality = stream.quality if getattr(stream, "quality", None) in valid_qualities else None
        stream_languages = getattr(stream, "languages", []) or []
        stream.filtered_languages = [lang for lang in stream_languages if lang in valid_languages] or [None]
        stream.cached = False

        if stream.filtered_resolution not in selected_resolutions_set:
            filtered_reasons["Resolution Not Selected"] += 1
            continue

        if (stream.size or 0) > user_data.max_size:
            filtered_reasons["Max Size Exceeded"] += 1
            continue

        if user_data.min_size > 0 and (stream.size or 0) > 0 and (stream.size or 0) < user_data.min_size:
            filtered_reasons["Min Size Not Met"] += 1
            continue

        if stream.filtered_quality not in quality_filter_set:
            filtered_reasons["Quality Not Selected"] += 1
            continue

        if not any(lang in language_filter_set for lang in stream.filtered_languages):
            filtered_reasons["Language Not Selected"] += 1
            continue

        if is_contain_18_plus_keywords(stream.name):
            filtered_reasons["Strict 18+ Keyword Filter"] += 1
            continue

        # Apply stream name include/exclude filter
        if stream_name_filter_mode != "disabled" and compiled_name_patterns:
            stream_name_lower = (stream.name or "").lower()
            if user_data.stream_name_filter_use_regex:
                matches_any = any(p.search(stream.name or "") for p in compiled_name_patterns)
            else:
                matches_any = any(p in stream_name_lower for p in compiled_name_patterns)

            if stream_name_filter_mode == "include" and not matches_any:
                filtered_reasons["Stream Name Filter"] += 1
                continue
            elif stream_name_filter_mode == "exclude" and matches_any:
                filtered_reasons["Stream Name Filter"] += 1
                continue

        filtered_streams.append(stream)

    if not filtered_streams:
        return filtered_streams, filtered_reasons

    # Step 2: Update cache status based on provider
    # Cache checking only applies to torrent streams (which have info_hash)
    has_info_hash = hasattr(filtered_streams[0], "info_hash") if filtered_streams else False
    if primary_provider and has_info_hash:
        service = primary_provider.service
        info_hashes = [stream.info_hash for stream in filtered_streams]

        # First check Redis cache
        cached_statuses = await get_cached_status(primary_provider, info_hashes)

        # Update streams with cached status from Redis
        uncached_streams = []
        for stream in filtered_streams:
            if cached_statuses.get(stream.info_hash, False):
                stream.cached = True
            else:
                stream.cached = False
                uncached_streams.append(stream)

        # For streams not found in Redis cache, use provider's cache check
        # but only if we haven't already checked this provider+media recently.
        if uncached_streams:
            already_checked = await is_cache_check_done(primary_provider, stremio_video_id)
            if not already_checked:
                cache_update_function = mapper.CACHE_UPDATE_FUNCTIONS.get(service)
                if cache_update_function:
                    try:
                        service_name = await cache_update_function(
                            streams=uncached_streams,
                            streaming_provider=primary_provider,
                            user_ip=user_ip,
                            stremio_video_id=stremio_video_id,
                        )
                        # Store only the cached ones in Redis
                        cached_info_hashes = [stream.info_hash for stream in uncached_streams if stream.cached]
                        if cached_info_hashes:
                            await store_cached_info_hashes(
                                primary_provider,
                                cached_info_hashes,
                                service_name,
                            )
                    except ProviderException as error:
                        logging.warning("Failed to update cache status for %s: %s", service, error)
                    except Exception as error:
                        logging.exception("Unexpected cache status update error for %s: %s", service, error)
                # Mark check as done regardless of results so we skip the API
                # call on subsequent requests within the TTL window.
                await mark_cache_check_done(primary_provider, stremio_video_id)

        if primary_provider.only_show_cached_streams:
            cached_filtered_streams = [stream for stream in filtered_streams if stream.cached]
            if not cached_filtered_streams:
                filtered_reasons["No Cached Streams"] = len(filtered_streams)
                return filtered_streams, filtered_reasons
            filtered_streams = cached_filtered_streams

    # Step 3: Dynamically sort streams based on user preferences
    def dynamic_sort_key(torrent_stream: AnyStreamData) -> tuple:
        def key_value(sorting_option: SortingOption) -> Any:
            key = sorting_option.key
            multiplier = 1 if sorting_option.direction == "asc" else -1

            match key:
                case "cached":
                    return multiplier * (1 if torrent_stream.cached else 0)
                case "resolution":
                    # Use user-defined resolution order for sorting (lower index = higher priority)
                    try:
                        rank = user_data.selected_resolutions.index(torrent_stream.filtered_resolution)
                    except ValueError:
                        rank = len(user_data.selected_resolutions)
                    return multiplier * -rank
                case "quality":
                    # Use user-defined quality order for sorting (lower index = higher priority)
                    try:
                        rank = user_data.quality_filter.index(torrent_stream.filtered_quality)
                    except ValueError:
                        # Try to find which group this quality belongs to
                        rank = len(user_data.quality_filter)
                        for i, group_name in enumerate(user_data.quality_filter):
                            if (
                                group_name in const.QUALITY_GROUPS
                                and torrent_stream.filtered_quality in const.QUALITY_GROUPS[group_name]
                            ):
                                rank = i
                                break
                    return multiplier * -rank
                case "size":
                    return multiplier * (torrent_stream.size or 0)
                case "seeders":
                    return multiplier * (getattr(torrent_stream, "seeders", 0) or 0)
                case "created_at":
                    created_at = torrent_stream.created_at
                    if isinstance(created_at, datetime):
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=UTC)
                        return multiplier * created_at.timestamp()
                    elif isinstance(created_at, (int, float)):
                        return multiplier * created_at
                    else:
                        return multiplier * datetime.min.replace(tzinfo=UTC).timestamp()
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

    # Step 5: Apply total stream cap
    limited_streams = limited_streams[: user_data.max_streams]

    return limited_streams, filtered_reasons


async def parse_stream_data(
    streams: list[AnyStreamData],
    user_data: UserData,
    secret_str: str,
    season: int = None,
    episode: int = None,
    user_ip: str | None = None,
    is_series: bool = False,
    return_rich: bool = False,
    is_usenet: bool = False,
    is_telegram: bool = False,
    is_http: bool = False,
    is_youtube: bool = False,
) -> list[Stream] | list[RichStream]:
    """
    Parse and format stream data for output.

    For multi-debrid support, this function iterates over all active providers
    and generates stream entries for each. For usenet streams, only usenet-capable
    providers are used. Telegram, HTTP, and YouTube streams bypass the provider
    loop entirely.

    Args:
        streams: Raw stream data of any supported type
        user_data: User preferences and configuration
        secret_str: Encrypted user data for URL generation
        season: Season number (for series)
        episode: Episode number (for series)
        user_ip: User's IP address
        is_series: Whether this is a series
        return_rich: If True, returns RichStream objects with full metadata.
                    If False, returns plain Stream objects for Stremio addon.
        is_usenet: Whether these are Usenet streams
        is_telegram: Whether these are Telegram streams
        is_http: Whether these are HTTP/direct streams
        is_youtube: Whether these are YouTube streams

    Returns:
        List of Stream or RichStream objects depending on return_rich parameter
    """
    if not streams:
        return []

    stremio_video_id = f"{streams[0].meta_id}:{season}:{episode}" if is_series else streams[0].meta_id

    # Determine which providers to generate streams for
    active_providers = user_data.get_active_providers()

    if is_telegram or is_http or is_youtube:
        # These types don't use debrid providers -- process once with no provider
        provider_list = [None]
    elif is_usenet:
        # Usenet: only use providers that support usenet
        provider_list = [p for p in active_providers if p.service in mapper.USENET_CAPABLE_PROVIDERS]
        if not provider_list:
            # No usenet-capable provider configured -- usenet streams can't be played
            return []
    else:
        # Torrent: only use providers that implement torrent playback.
        # Usenet-only providers (sabnzbd/nzbget/nzbdav/easynews/stremio_nntp) must not
        # produce /playback URLs for torrent streams.
        torrent_capable_providers = [p for p in active_providers if p.service in mapper.GET_VIDEO_URL_FUNCTIONS]
        if torrent_capable_providers:
            provider_list = torrent_capable_providers
        else:
            # Fallback to direct P2P only when there is no torrent-capable provider.
            provider_list = [None] if "p2p" not in settings.disabled_providers else []

    # Process each provider concurrently for cache checking and stream building
    async def _process_single_provider(
        current_provider: StreamingProvider | None,
    ) -> tuple[list[Stream] | list[RichStream], dict]:
        """Process a single provider: filter, cache-check, and build stream entries."""
        filtered_streams, filtered_reasons = await filter_and_sort_streams(
            streams, user_data, stremio_video_id, user_ip, provider_override=current_provider
        )

        if not filtered_streams:
            return [], filtered_reasons

        # --- Per-provider naming and URL setup ---
        has_streaming_provider = current_provider is not None
        provider_name = current_provider.name if current_provider else "default"
        streaming_provider_name = (
            STREAMING_PROVIDERS_SHORT_NAMES.get(current_provider.service, "P2P") if current_provider else "P2P"
        )

        # Stream type indicator for addon name
        if is_telegram:
            stream_type_indicator = "📱"
        elif is_usenet:
            stream_type_indicator = "📰"
        elif is_youtube:
            stream_type_indicator = "▶️"
        else:
            stream_type_indicator = ""
        addon_name = f"{settings.addon_name} {streaming_provider_name} {stream_type_indicator}".strip()

        # Telegram, HTTP, and YouTube streams don't require a debrid provider
        if is_telegram or is_http or is_youtube:
            has_streaming_provider = True
            base_proxy_url_template = ""
        elif not has_streaming_provider:
            # P2P mode (torrent only) -- no URL template needed, will use magnet links
            base_proxy_url_template = ""
        else:
            # Show MediaFlow indicator if the provider has it enabled
            if (
                user_data.mediaflow_config
                and user_data.mediaflow_config.proxy_url
                and user_data.mediaflow_config.api_password
                and current_provider.use_mediaflow
            ):
                addon_name += " 🕵🏼‍♂️"

            # Build URL template for this provider
            if is_usenet:
                base_proxy_url_template = (
                    f"{settings.host_url}/streaming_provider/{secret_str}/usenet/{provider_name}/{{}}"
                )
            else:
                base_proxy_url_template = (
                    f"{settings.host_url}/streaming_provider/{secret_str}/playback/{provider_name}/{{}}"
                )

        # --- Generate stream entries for this provider ---
        provider_streams = _build_stream_entries(
            filtered_streams=filtered_streams,
            user_data=user_data,
            secret_str=secret_str,
            season=season,
            episode=episode,
            is_series=is_series,
            is_usenet=is_usenet,
            is_telegram=is_telegram,
            is_http=is_http,
            is_youtube=is_youtube,
            return_rich=return_rich,
            has_streaming_provider=has_streaming_provider,
            current_provider=current_provider,
            streaming_provider_name=streaming_provider_name,
            addon_name=addon_name,
            base_proxy_url_template=base_proxy_url_template,
        )
        return provider_streams, filtered_reasons

    # Run all providers in parallel
    provider_results = await asyncio.gather(*[_process_single_provider(p) for p in provider_list])

    per_provider_streams: list[list[Stream] | list[RichStream]] = []
    last_filtered_reasons: dict = {}
    for provider_streams, filtered_reasons in provider_results:
        last_filtered_reasons = filtered_reasons
        if provider_streams:
            per_provider_streams.append(provider_streams)

    # Combine provider streams based on user's provider grouping preference
    all_stream_list: list[Stream] | list[RichStream] = []
    if per_provider_streams:
        if user_data.provider_grouping == "mixed" and len(per_provider_streams) > 1:
            # Interleave streams round-robin across providers (already in priority order)
            iterators = [iter(lst) for lst in per_provider_streams]
            while iterators:
                exhausted = []
                for i, it in enumerate(iterators):
                    val = next(it, None)
                    if val is not None:
                        all_stream_list.append(val)
                    else:
                        exhausted.append(i)
                for i in reversed(exhausted):
                    iterators.pop(i)
        else:
            # "separate" mode: concatenate in provider priority order (default)
            for provider_streams in per_provider_streams:
                all_stream_list.extend(provider_streams)

    # If no streams produced across all providers, show filter reason message
    if not all_stream_list:
        # Check if there are no providers at all (and not telegram/P2P-capable)
        if not active_providers and not is_telegram:
            no_provider_stream = create_exception_stream(
                settings.addon_name,
                "🔧 No Debrid Provider Configured\n\n"
                "Please configure a debrid service in your profile to stream content.\n\n"
                "Supported providers:\n"
                "• Real-Debrid • AllDebrid • TorBox\n"
                "• Premiumize • Debrid-Link • Offcloud\n"
                "• Easydebrid • PikPak • StremThru",
                "configure_debrid.mp4",
            )
            if return_rich:
                return []
            return [no_provider_stream]

        # All streams were filtered out
        reason_data = [f"{count} x {reason}" for reason, count in last_filtered_reasons.items() if count > 0]
        reasons = "\n".join(reason_data)
        exception_stream = create_exception_stream(
            settings.addon_name,
            f"🚫 Streams Found\n⚙️ Filtered by your configuration preferences\n{reasons}",
            "filtered_no_streams.mp4",
        )
        if return_rich:
            return []
        return [exception_stream]

    return all_stream_list


def _build_stream_entries(
    filtered_streams: list[AnyStreamData],
    user_data: UserData,
    secret_str: str,
    season: int | None,
    episode: int | None,
    is_series: bool,
    is_usenet: bool,
    is_telegram: bool,
    is_http: bool,
    is_youtube: bool,
    return_rich: bool,
    has_streaming_provider: bool,
    current_provider: StreamingProvider | None,
    streaming_provider_name: str,
    addon_name: str,
    base_proxy_url_template: str,
) -> list[Stream] | list[RichStream]:
    """Build Stremio stream entries for a single provider from filtered streams."""
    stream_list = []

    for stream_data in filtered_streams:
        # Get episode file variants for series content
        # Torrent/Usenet have get_episode_files(); Telegram/HTTP use direct attributes
        if is_series:
            if hasattr(stream_data, "get_episode_files"):
                episode_variants = stream_data.get_episode_files(season, episode)
            else:
                # Telegram/HTTP: no file list, treat as single stream
                episode_variants = [None]
        else:
            episode_variants = [None]
        if is_series and not episode_variants:
            continue

        for episode_data in episode_variants:
            if episode_data:
                file_name = episode_data.filename
                file_index = episode_data.file_index
            elif hasattr(stream_data, "get_main_file"):
                # Torrent/Usenet: get main file from files list
                main_file = stream_data.get_main_file()
                if main_file:
                    file_name = main_file.filename
                    file_index = main_file.file_index
                else:
                    files = getattr(stream_data, "files", [])
                    file_name = files[0].filename if files else None
                    file_index = files[0].file_index if files else 0
            else:
                # Telegram/HTTP: use direct attributes
                file_name = getattr(stream_data, "file_name", None) or stream_data.name
                file_index = 0

            # make sure file_name is basename
            file_name = basename(file_name) if file_name else None

            # Compute quality_detail - use getattr for attributes not present on all stream types
            hdr_formats = getattr(stream_data, "hdr_formats", []) or []
            audio_formats = getattr(stream_data, "audio_formats", []) or []
            quality_detail = " ".join(
                filter(
                    None,
                    [
                        f"🎨 {'|'.join(hdr_formats)}" if hdr_formats else None,
                        f"📺 {stream_data.quality}" if stream_data.quality else None,
                        f"🎞️ {stream_data.codec}" if stream_data.codec else None,
                        f"🎵 {'|'.join(audio_formats)}" if audio_formats else None,
                    ],
                )
            )

            resolution = stream_data.resolution.upper() if stream_data.resolution else "N/A"
            # Only show cache status when there's a debrid provider
            if has_streaming_provider:
                streaming_provider_status = "⚡️" if getattr(stream_data, "cached", False) else "⏳"
            else:
                streaming_provider_status = ""  # P2P mode - no cache status
            # seeders is torrent-specific; usenet has grabs; telegram/http have neither
            seeders = getattr(stream_data, "seeders", None)
            seeders_info = f"👤 {seeders}" if seeders is not None else None
            stream_size = stream_data.size or 0
            if episode_data and episode_data.size:
                file_size = episode_data.size
                size_info = f"{convert_bytes_to_readable(file_size)} / {convert_bytes_to_readable(stream_size)}"
            else:
                file_size = stream_size
                size_info = convert_bytes_to_readable(file_size)

            # Language names for display
            languages = getattr(stream_data, "languages", []) or []
            display_languages = list(languages)
            # Language flags for template
            language_flags = list(
                filter(
                    None,
                    [const.LANGUAGE_COUNTRY_FLAGS.get(lang) for lang in languages],
                )
            )

            languages_str = f"🌐 {' + '.join(display_languages)}" if display_languages else None
            source_info = f"🔗 {stream_data.source}"
            uploader = getattr(stream_data, "uploader", None)
            if uploader:
                source_info += f" 🧑‍💻 {uploader}"

            description = "\n".join(
                filter(
                    None,
                    [
                        quality_detail,
                        " ".join(filter(None, [size_info, seeders_info])),
                        languages_str,
                        source_info,
                    ],
                )
            )

            # Build the stream URL
            stream_url = None
            nzb_direct_url = None
            info_hash = None
            file_idx = None
            sources = None
            stream_id = None

            # Handle Telegram streams differently
            if is_youtube:
                # YouTube: use ytId field — no URL needed
                stream_id = stream_data.video_id
            elif is_http:
                # HTTP: use the direct URL
                stream_url = stream_data.url
                stream_id = str(stream_data.stream_id)
            elif is_telegram:
                # Telegram streams use chat_id and message_id for playback
                chat_id = getattr(stream_data, "chat_id", None)
                msg_id = getattr(stream_data, "message_id", None)
                if chat_id and msg_id:
                    # Telegram streams don't need debrid provider - direct playback
                    # Route is: /streaming_provider/{secret_str}/telegram/{chat_id}/{message_id}
                    stream_url = f"{settings.host_url}/streaming_provider/{secret_str}/telegram/{chat_id}/{msg_id}"
                    stream_id = f"{chat_id}:{msg_id}"
                else:
                    # Skip if missing required fields
                    logging.warning(f"Telegram stream missing chat_id or message_id: {stream_data}")
                    continue
            elif is_usenet:
                # Usenet: use nzb_guid as stream identifier
                stream_id = stream_data.nzb_guid
                if current_provider and current_provider.service == "stremio_nntp":
                    # Direct NZB streaming — Stremio v5 fetches the NZB and handles NNTP natively.
                    # For file-imported NZBs (nzb_url is None), generate a signed expiring URL.
                    # For externally-sourced NZBs (scraped/URL-imported), use the original URL.
                    if stream_data.nzb_url:
                        nzb_direct_url = stream_data.nzb_url
                    else:
                        nzb_direct_url = generate_signed_nzb_url(stream_data.nzb_guid)
                elif has_streaming_provider:
                    stream_url = base_proxy_url_template.format(stream_id)
                    if episode_data:
                        stream_url += f"/{season}/{episode}"
                    if file_name:
                        stream_url += f"/{quote(file_name)}"
                    # Mark addon playback URLs so backend can apply per-provider MediaFlow toggle only here.
                    stream_url += "?stremio=1"
                else:
                    # Usenet streams require a provider - can't be played directly
                    continue
            else:
                # Torrent: use info_hash as stream identifier
                stream_id = stream_data.info_hash
                if has_streaming_provider:
                    stream_url = base_proxy_url_template.format(stream_id)
                    if episode_data:
                        stream_url += f"/{season}/{episode}"
                    if file_name:
                        stream_url += f"/{quote(file_name)}"
                    # Mark addon playback URLs so backend can apply per-provider MediaFlow toggle only here.
                    stream_url += "?stremio=1"
                else:
                    info_hash = stream_data.info_hash
                    file_idx = file_index
                    announce_list = getattr(stream_data, "announce_list", None) or TRACKERS
                    sources = [f"tracker:{tracker}" for tracker in announce_list] + [f"dht:{stream_data.info_hash}"]

            # Build context for template rendering
            if is_youtube:
                stream_type = "youtube"
            elif is_http:
                stream_type = "http"
            elif is_telegram:
                stream_type = "telegram"
            elif is_usenet:
                stream_type = "usenet"
            else:
                stream_type = "torrent"

            cached = getattr(stream_data, "cached", False)
            stream_context = {
                "name": stream_data.name,
                "filename": file_name,
                "type": stream_type,
                "resolution": resolution,
                "quality": getattr(stream_data, "quality", None),
                "codec": getattr(stream_data, "codec", None),
                "bit_depth": getattr(stream_data, "bit_depth", None),
                "audio_formats": list(audio_formats),
                "channels": list(getattr(stream_data, "channels", []) or []),
                "hdr_formats": list(hdr_formats),
                "languages": display_languages,
                "language_flags": language_flags,
                "size": file_size,
                "seeders": seeders,
                "source": stream_data.source,
                "release_group": getattr(stream_data, "release_group", None),
                "uploader": uploader,
                "cached": cached,
            }
            service_context = {
                "name": streaming_provider_name,
                "shortName": STREAMING_PROVIDERS_SHORT_NAMES.get(
                    current_provider.service if current_provider else "", "P2P"
                ),
                "cached": cached if has_streaming_provider else False,
            }
            addon_context = {"name": settings.addon_name}

            # Get template (user's or default)
            template = user_data.stream_template or StreamTemplate()

            # Render title and description using templates
            try:
                stream_name = render_stream_template(
                    template.title,
                    stream_context,
                    service=service_context,
                    addon=addon_context,
                )
                description = render_stream_template(
                    template.description,
                    stream_context,
                    service=service_context,
                    addon=addon_context,
                )
            except Exception as e:
                # Fall back to hardcoded format if template fails
                logging.warning(f"Template rendering failed: {e}")
                if has_streaming_provider:
                    stream_name = f"{addon_name} {resolution} {streaming_provider_status}"
                else:
                    stream_name = f"{addon_name} {resolution}"

            # Create the Stremio Stream object
            stremio_stream = Stream(
                name=stream_name,
                description=description,
                url=stream_url,
                nzbUrl=nzb_direct_url,
                ytId=stream_data.video_id if is_youtube else None,
                infoHash=info_hash,
                fileIdx=file_idx,
                sources=sources,
                behaviorHints=StreamBehaviorHints(
                    bingeGroup=f"{settings.addon_name.replace(' ', '-')}-{quality_detail}-{resolution}",
                    filename=file_name or stream_data.name,
                    videoSize=file_size or None,
                ),
            )

            if return_rich:
                # Create rich metadata for frontend
                # Determine appropriate seeders/grabs metric per stream type
                if is_usenet:
                    rich_seeders = getattr(stream_data, "grabs", 0) or 0
                else:
                    rich_seeders = seeders or 0
                rich_metadata = RichStreamMetadata(
                    id=stream_id,
                    info_hash=stream_id,  # For Usenet this is nzb_guid, for Telegram chat:msg
                    name=stream_data.name,
                    resolution=stream_data.resolution,
                    quality=getattr(stream_data, "quality", None),
                    codec=getattr(stream_data, "codec", None),
                    audio_formats=list(audio_formats),
                    hdr_formats=list(hdr_formats),
                    source=stream_data.source or "Unknown",
                    languages=list(languages),
                    size=file_size,
                    size_display=size_info.replace("💾 ", "") if size_info else None,
                    seeders=rich_seeders,
                    uploader=uploader or getattr(stream_data, "poster", None),
                    uploaded_at=stream_data.created_at.isoformat() if stream_data.created_at else None,
                    cached=cached,
                )
                stream_list.append(RichStream(stream=stremio_stream, metadata=rich_metadata))
            else:
                stream_list.append(stremio_stream)

    return stream_list


def extract_stremio_streams(rich_streams: list[RichStream]) -> list[Stream]:
    """Extract just the Stremio Stream objects from RichStream list."""
    return [rs.stream for rs in rich_streams]


@functools.lru_cache(maxsize=1024)
def convert_bytes_to_readable(size_bytes: int) -> str:
    """
    Convert a size in bytes into a more human-readable format.
    """
    if not size_bytes or size_bytes <= 0:
        return "💾 0 B"

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


async def parse_tv_stream_data(tv_streams: list[TVStreams], user_data: UserData) -> list[Stream]:
    is_mediaflow_proxy_enabled = user_data.mediaflow_config and user_data.mediaflow_config.proxy_live_streams
    addon_name = f"{settings.addon_name} {'🕵🏼‍♂️' if is_mediaflow_proxy_enabled else '📡'}"

    stream_processor = functools.partial(
        process_stream,
        is_mediaflow_proxy_enabled=is_mediaflow_proxy_enabled,
        mediaflow_config=user_data.mediaflow_config,
        addon_name=addon_name,
    )

    processed_streams = await asyncio.gather(*[stream_processor(stream) for stream in reversed(tv_streams)])

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
) -> Stream | str | None:
    if settings.validate_m3u8_urls_liveness:
        is_working = await validate_m3u8_or_mpd_url_with_cache(stream.url, stream.behaviorHints or {})
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
    endpoint = "/proxy/mpd/manifest.m3u8" if stream.drm_key else "/proxy/hls/manifest.m3u8"
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
        response_headers=stream.behaviorHints.get("proxyHeaders", {}).get("response", {}),
        encryption_api_password=mediaflow_config.api_password,
    )


def create_exception_stream(addon_name: str, description: str, exc_file_name: str) -> Stream:
    return Stream(
        name=addon_name,
        description=description,
        url=f"{settings.host_url}/static/exceptions/{exc_file_name}",
        behaviorHints={"notWebReady": True},
    )


async def fetch_downloaded_info_hashes(
    user_data: UserData,
    user_ip: str | None,
    provider: StreamingProvider | None = None,
) -> list[str]:
    """
    Fetch downloaded info hashes from a streaming provider's watchlist.

    Args:
        user_data: User configuration
        user_ip: User's public IP for API calls
        provider: Specific provider to fetch from. If None, uses the primary provider.
    """
    target_provider = provider or user_data.get_primary_provider()
    if not target_provider:
        return []

    if fetch_downloaded_info_hashes_function := mapper.FETCH_DOWNLOADED_INFO_HASHES_FUNCTIONS.get(
        target_provider.service
    ):
        try:
            downloaded_info_hashes = await fetch_downloaded_info_hashes_function(
                streaming_provider=target_provider, user_ip=user_ip
            )
            return downloaded_info_hashes
        except Exception as error:
            logging.exception(f"Failed to fetch downloaded info hashes for {target_provider.service}: {error}")
            pass

    return []


async def generate_manifest(user_data: UserData, genres: dict) -> dict:
    addon_name = settings.addon_name

    # Collect all active providers and their watchlist settings
    active_providers = user_data.get_active_providers()
    watchlist_providers = []
    provider_short_names = []

    for provider in active_providers:
        short_name = STREAMING_PROVIDERS_SHORT_NAMES.get(provider.service, provider.service[:2].upper())
        provider_short_names.append(short_name)
        # Only include providers that have watchlist support AND have it enabled
        if provider.enable_watchlist_catalogs and provider.service in mapper.FETCH_DOWNLOADED_INFO_HASHES_FUNCTIONS:
            watchlist_providers.append(
                {
                    "service": provider.service,
                    "short_name": short_name,
                }
            )

    # Build addon name with all provider short names (e.g., "MediaFusion RD+TRB")
    if provider_short_names:
        addon_name += f" {'+'.join(provider_short_names)}"

    if user_data.mediaflow_config:
        addon_name += " 🕵🏼‍♂️"

    # Build unique addon ID suffix from all active provider services
    provider_id_suffix = ".".join(p.service for p in active_providers) if active_providers else ""

    mdblist_data = {}
    if user_data.mdblist_config:
        mdblist_data = {
            f"mdblist_{mdblist.catalog_type}_{mdblist.id}": mdblist.model_dump(include={"title", "catalog_type"})
            for mdblist in user_data.mdblist_config.lists
        }

    selected_catalogs = [
        cid for cid in user_data.get_enabled_catalog_ids() if not cid.startswith("mdblist_") or cid in mdblist_data
    ]
    # Personal library catalogs require an authenticated user context.
    # Hide them from anonymous manifests so they don't appear with empty results.
    if not user_data.user_id:
        selected_catalogs = [cid for cid in selected_catalogs if not cid.startswith("my_library_")]

    manifest_data = {
        "addon_name": addon_name,
        "version": settings.version,
        "contact_email": settings.contact_email,
        "description": settings.description,
        "logo_url": settings.logo_url,
        "provider_id_suffix": provider_id_suffix,
        "enable_imdb_metadata": user_data.enable_imdb_metadata,
        "enable_catalogs": user_data.enable_catalogs,
        "watchlist_providers": watchlist_providers,
        "selected_catalogs": selected_catalogs,
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


# Shared blocklist / allowlist for filtering non-video torrent titles.
# Used by both Scrapy spiders and indexer scrapers.
# fmt: off
NON_VIDEO_BLOCKLIST_KEYWORDS = [
    ".exe", ".zip", ".rar", ".iso", ".bin", ".tar", ".7z", ".pdf", ".xyz",
    ".epub", ".mobi", ".azw3", ".doc", ".docx", ".txt", ".rtf",
    "setup", "install", "crack", "patch", "trainer", "readme",
    "manual", "keygen", "license", "tutorial", "ebook", "software", "book",
    "repack", "fitgirl",
]

VIDEO_ALLOWLIST_KEYWORDS = [
    "mkv", "mp4", "avi", ".webm", ".mov", ".flv", "webdl", "web-dl", "webrip", "bluray",
    "brrip", "bdrip", "dvdrip", "hdtv", "hdcam", "hdrip", "1080p", "720p", "480p", "360p",
    "2160p", "4k", "x264", "x265", "hevc", "h264", "h265", "aac", "xvid", "movie", "series", "season",
]
# fmt: on


def is_non_video_title(title: str) -> bool:
    """Return True if the title looks like a game, application, or other non-video content."""
    lower = title.lower()
    return any(kw in lower for kw in NON_VIDEO_BLOCKLIST_KEYWORDS)


def calculate_max_similarity_ratio(torrent_title: str, title: str, aka_titles: list[str] | None = None) -> int:
    # Check similarity with the main title
    title_similarity_ratio = fuzz.ratio(torrent_title.lower(), title.lower())

    # Check similarity with aka titles
    aka_similarity_ratios = (
        [fuzz.ratio(torrent_title.lower(), aka_title.lower()) for aka_title in aka_titles] if aka_titles else []
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
                if levels.index(level) > levels.index(highest_level) if highest_level in levels else -1:
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


# =============================================================================
# STREAM TEMPLATE RENDERER (uses template_engine.py)
# =============================================================================


def render_stream_template(
    template: str,
    stream: Any,
    service: dict | None = None,
    addon: dict | None = None,
) -> str:
    """
    Render a stream template with variable interpolation.

    Uses the template_engine module for AIOStreams-compatible template syntax:
    {stream.field::modifier["if_true"||"if_false"]}

    Args:
        template: Template string
        stream: Stream data (dict or object with attributes)
        service: Service info dict with name, shortName, cached fields
        addon: Addon info dict with name field

    Returns:
        Rendered template string
    """
    # Convert stream to dict if it's an object
    if isinstance(stream, dict):
        stream_data = stream
    elif hasattr(stream, "__dict__"):
        stream_data = stream.__dict__
    elif hasattr(stream, "model_dump"):
        stream_data = stream.model_dump()
    else:
        stream_data = {}

    # Build context for variable lookup
    context = {
        "stream": stream_data,
        "service": service or {},
        "addon": addon or {"name": settings.addon_name},
    }

    return engine_render_template(template, context)


def format_stream_for_stremio(
    stream: Any,
    user_data: Optional["UserData"] = None,
    service_name: str | None = None,
    is_cached: bool = False,
) -> tuple[str, str]:
    """
    Format a stream for Stremio display using user's template or defaults.

    Args:
        stream: Stream data object
        user_data: User configuration with optional stream_template
        service_name: Debrid service name
        is_cached: Whether the stream is cached

    Returns:
        tuple of (title, description) for Stremio stream
    """
    # Get template from user config or use defaults
    if user_data and user_data.stream_template:
        template = user_data.stream_template
    else:
        template = StreamTemplate()

    # Build service context
    service = None
    if service_name:
        short_name = STREAMING_PROVIDERS_SHORT_NAMES.get(service_name, service_name[:2].upper())
        service = {
            "name": service_name,
            "shortName": short_name,
            "cached": is_cached,
        }

    # Build addon context
    addon = {"name": settings.addon_name}

    # Render title and description
    title = render_stream_template(template.title, stream, service=service, addon=addon)
    description = render_stream_template(template.description, stream, service=service, addon=addon)

    return title, description
