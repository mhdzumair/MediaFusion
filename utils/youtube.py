"""
YouTube video analysis utilities using yt-dlp.

This module provides a centralized interface for fetching YouTube video metadata,
used by both the Telegram bot and the web API import endpoints.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
import yt_dlp

from db.config import settings

logger = logging.getLogger(__name__)

# Pattern to extract YouTube video ID from various URL formats
YOUTUBE_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})"
)
ISO_8601_DURATION_PATTERN = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


@dataclass
class YouTubeVideoInfo:
    """Container for YouTube video metadata."""

    video_id: str
    title: str
    description: str | None = None
    duration: int = 0  # Duration in seconds
    channel: str | None = None
    channel_id: str | None = None
    upload_date: str | None = None  # YYYYMMDD format
    view_count: int | None = None
    like_count: int | None = None
    thumbnail: str | None = None
    is_live: bool = False
    categories: list[str] | None = None
    tags: list[str] | None = None
    resolution: str | None = None
    # Geo restriction metadata from YouTube regionRestriction:
    # - allowed: video is only available in listed countries
    # - blocked: video is blocked in listed countries
    geo_restriction_type: str | None = None  # allowed | blocked
    geo_restriction_countries: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "video_id": self.video_id,
            "title": self.title,
            "description": self.description,
            "duration": self.duration,
            "channel": self.channel,
            "channel_id": self.channel_id,
            "upload_date": self.upload_date,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "thumbnail": self.thumbnail,
            "is_live": self.is_live,
            "categories": self.categories,
            "tags": self.tags,
            "resolution": self.resolution,
            "geo_restriction_type": self.geo_restriction_type,
            "geo_restriction_countries": self.geo_restriction_countries,
        }


def extract_video_id(url: str) -> str | None:
    """
    Extract video ID from a YouTube URL.

    Args:
        url: YouTube URL in any format (watch, shorts, youtu.be)

    Returns:
        Video ID string or None if not found
    """
    match = YOUTUBE_URL_PATTERN.search(url)
    return match.group(1) if match else None


def _get_best_resolution(formats: list[dict]) -> str | None:
    """Determine the best available resolution from format list."""
    max_height = 0
    for fmt in formats:
        height = fmt.get("height")
        if height and isinstance(height, int) and height > max_height:
            max_height = height

    if max_height >= 2160:
        return "2160p"
    if max_height >= 1440:
        return "1440p"
    if max_height >= 1080:
        return "1080p"
    if max_height >= 720:
        return "720p"
    if max_height >= 480:
        return "480p"
    if max_height > 0:
        return f"{max_height}p"
    return None


def _parse_iso8601_duration(duration: str | None) -> int:
    """Convert ISO-8601 duration (e.g., PT1H2M3S) to seconds."""
    if not duration:
        return 0
    match = ISO_8601_DURATION_PATTERN.fullmatch(duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _normalize_country_list(countries: list[str] | None) -> list[str]:
    """Normalize country list values and keep insertion order."""
    if not countries:
        return []

    seen: set[str] = set()
    normalized: list[str] = []
    for country in countries:
        value = (country or "").strip()
        if not value:
            continue
        if len(value) == 2:
            value = value.upper()
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


def _split_country_text(raw_countries: str) -> list[str]:
    """Split country names/codes from free-form text."""
    normalized = raw_countries.replace(" and ", ",").replace(";", ",").replace("|", ",").replace("/", ",")
    return _normalize_country_list([part.strip(" .") for part in normalized.split(",")])


def _extract_geo_restriction_from_error_message(error_message: str) -> tuple[str | None, list[str]]:
    """
    Extract geo restriction semantics from yt-dlp error text.

    Returns:
        Tuple of (restriction_type, countries).
    """
    message = error_message.strip()
    lower_message = message.lower()

    # Common YouTube phrasing:
    # "This video is available in India."
    # "This video is available in the following countries: IN, US."
    available_match = re.search(
        r"this video is available in(?: the following countries:)?\s*([^.]+)\.?",
        message,
        flags=re.IGNORECASE,
    )
    if available_match:
        return "allowed", _split_country_text(available_match.group(1))

    # Fallback for phrasing such as "blocked in XX, YY"
    blocked_match = re.search(
        r"(?:blocked|not available) in(?: the following countries:)?\s*([^.]+)\.?",
        message,
        flags=re.IGNORECASE,
    )
    if blocked_match:
        return "blocked", _split_country_text(blocked_match.group(1))

    # If no country list is present but error clearly indicates geo restriction,
    # retain the semantic (unknown countries).
    if "not made this video available in your country" in lower_message:
        return "allowed", []
    if "not available in your country" in lower_message or "geo restriction" in lower_message:
        return "blocked", []

    return None, []


def _extract_geo_restriction_from_info(info: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Extract geo restriction metadata from yt-dlp info dict when available."""
    # Some extractors expose region_restriction={"allowed":[...]} or {"blocked":[...]}
    region_restriction = info.get("region_restriction")
    if isinstance(region_restriction, dict):
        allowed = _normalize_country_list(region_restriction.get("allowed"))
        blocked = _normalize_country_list(region_restriction.get("blocked"))
        if allowed:
            return "allowed", allowed
        if blocked:
            return "blocked", blocked

    # Additional possible keys in extractor output
    allowed_regions = _normalize_country_list(info.get("regions_allowed") or info.get("available_countries"))
    if allowed_regions:
        return "allowed", allowed_regions

    blocked_regions = _normalize_country_list(info.get("regions_blocked"))
    if blocked_regions:
        return "blocked", blocked_regions

    return None, []


def _pick_best_thumbnail_from_api(thumbnails: dict[str, Any]) -> str | None:
    """Pick best thumbnail URL from YouTube Data API thumbnail object."""
    if not thumbnails:
        return None

    for key in ("maxres", "standard", "high", "medium", "default"):
        thumb = thumbnails.get(key)
        if isinstance(thumb, dict) and thumb.get("url"):
            return thumb["url"]

    # Fallback: choose the largest width if keys are non-standard
    best_url = None
    best_width = -1
    for thumb in thumbnails.values():
        if not isinstance(thumb, dict):
            continue
        url = thumb.get("url")
        width = thumb.get("width", 0)
        if url and width >= best_width:
            best_url = url
            best_width = width
    return best_url


def _is_geo_restriction_error(error_message: str) -> bool:
    """Check whether an error is likely caused by geo restriction."""
    lower_message = error_message.lower()
    geo_hints = (
        "not made this video available in your country",
        "not available in your country",
        "geo restriction",
        "this video is available in",
    )
    return any(hint in lower_message for hint in geo_hints)


def _is_video_unavailable_ytdlp_error(error_message: str) -> bool:
    """yt-dlp errors when the video is private, deleted, or region-blocked without geo phrasing."""
    lower_message = error_message.lower()
    hints = (
        "this video is not available",
        "video is not available",
        "video unavailable",
        "private video",
        "no longer available",
        "has been removed",
        "this video has been deleted",
        "members only",
        "sign in to confirm your age",
    )
    return any(h in lower_message for h in hints)


def _fetch_video_info_sync(video_id: str) -> dict[str, Any]:
    """
    Synchronous function to fetch video info using yt-dlp.
    This is run in a thread pool to avoid blocking the event loop.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        # Don't process video formats - we just need metadata
        "format": "best",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


async def _fetch_youtube_data_api_metadata(video_id: str) -> dict[str, Any]:
    """
    Fetch metadata from YouTube Data API (if configured).

    This helps capture regionRestriction.allowed/blocked even when yt-dlp
    cannot extract due to server geo restrictions.
    """
    if not settings.youtube_api_key:
        return {}

    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "id": video_id,
        "part": "snippet,contentDetails,status",
        "key": settings.youtube_api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning(f"YouTube Data API request failed for {video_id}: {exc}")
        return {}

    items = data.get("items") or []
    if not items:
        return {}

    item = items[0]
    snippet = item.get("snippet") or {}
    content_details = item.get("contentDetails") or {}
    status = item.get("status") or {}
    region_restriction = status.get("regionRestriction") or {}

    allowed = _normalize_country_list(region_restriction.get("allowed"))
    blocked = _normalize_country_list(region_restriction.get("blocked"))

    geo_restriction_type = None
    geo_restriction_countries: list[str] = []
    if allowed:
        geo_restriction_type = "allowed"
        geo_restriction_countries = allowed
    elif blocked:
        geo_restriction_type = "blocked"
        geo_restriction_countries = blocked

    return {
        "title": snippet.get("title"),
        "channel": snippet.get("channelTitle"),
        "channel_id": snippet.get("channelId"),
        "thumbnail": _pick_best_thumbnail_from_api(snippet.get("thumbnails") or {}),
        "duration": _parse_iso8601_duration(content_details.get("duration")),
        "is_live": snippet.get("liveBroadcastContent") == "live",
        "geo_restriction_type": geo_restriction_type,
        "geo_restriction_countries": geo_restriction_countries,
    }


async def _fetch_youtube_oembed_metadata(video_id: str) -> dict[str, Any]:
    """Fetch lightweight public metadata via YouTube oEmbed (no API key needed)."""
    url = "https://www.youtube.com/oembed"
    params = {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "format": "json",
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return {}

    return {
        "title": data.get("title"),
        "channel": data.get("author_name"),
        "thumbnail": data.get("thumbnail_url"),
    }


def format_geo_restriction_label(
    geo_restriction_type: str | None,
    geo_restriction_countries: list[str] | None,
    *,
    max_countries: int = 3,
) -> str | None:
    """Build a short display label for stream title/description."""
    if geo_restriction_type not in {"allowed", "blocked"}:
        return None

    countries = _normalize_country_list(geo_restriction_countries)
    if countries:
        shown = ", ".join(countries[:max_countries])
        if len(countries) > max_countries:
            shown = f"{shown} +{len(countries) - max_countries} more"
    else:
        shown = "selected regions"

    if geo_restriction_type == "allowed":
        return f"Geo: only in {shown}"
    return f"Geo: blocked in {shown}"


async def analyze_youtube_video(video_id: str) -> YouTubeVideoInfo:
    """
    Fetch YouTube video metadata using yt-dlp.

    Args:
        video_id: YouTube video ID (11 character string)

    Returns:
        YouTubeVideoInfo object with video metadata

    Raises:
        ValueError: If video_id is invalid
        Exception: If yt-dlp fails to fetch the video info and no fallback is available
    """
    if not video_id or len(video_id) != 11:
        raise ValueError(f"Invalid video ID: {video_id}")

    logger.debug(f"Fetching YouTube video info for: {video_id}")
    api_metadata = await _fetch_youtube_data_api_metadata(video_id)

    geo_restriction_type = api_metadata.get("geo_restriction_type")
    geo_restriction_countries = _normalize_country_list(api_metadata.get("geo_restriction_countries"))

    loop = asyncio.get_running_loop()
    try:
        # Run yt-dlp in a thread pool to avoid blocking
        info = await loop.run_in_executor(None, _fetch_video_info_sync, video_id)
    except Exception as exc:
        error_message = str(exc)
        extracted_type, extracted_countries = _extract_geo_restriction_from_error_message(error_message)
        if not geo_restriction_type and extracted_type:
            geo_restriction_type = extracted_type
            geo_restriction_countries = extracted_countries

        # If yt-dlp failed due to geo restrictions or generic unavailability, still return
        # metadata from YouTube Data API / oEmbed so the user can import and label correctly.
        if _is_geo_restriction_error(error_message) or _is_video_unavailable_ytdlp_error(error_message):
            oembed_metadata = await _fetch_youtube_oembed_metadata(video_id)
            fallback_title = api_metadata.get("title") or oembed_metadata.get("title") or f"YouTube Video ({video_id})"
            fallback_channel = api_metadata.get("channel") or oembed_metadata.get("channel")
            fallback_thumbnail = (
                api_metadata.get("thumbnail")
                or oembed_metadata.get("thumbnail")
                or f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
            )

            return YouTubeVideoInfo(
                video_id=video_id,
                title=fallback_title,
                duration=api_metadata.get("duration", 0) or 0,
                channel=fallback_channel,
                channel_id=api_metadata.get("channel_id"),
                thumbnail=fallback_thumbnail,
                is_live=bool(api_metadata.get("is_live", False)),
                resolution=None,
                geo_restriction_type=geo_restriction_type,
                geo_restriction_countries=geo_restriction_countries or None,
            )

        raise

    # Extract best resolution from available formats
    resolution = None
    if "formats" in info:
        resolution = _get_best_resolution(info["formats"])

    # Get the best thumbnail
    thumbnail = None
    thumbnails = info.get("thumbnails", [])
    if thumbnails:
        # Sort by preference/width and get the best one
        sorted_thumbnails = sorted(
            thumbnails,
            key=lambda x: (x.get("preference", 0), x.get("width", 0)),
            reverse=True,
        )
        thumbnail = sorted_thumbnails[0].get("url") if sorted_thumbnails else None

    # Prefer explicit regionRestriction from API; fall back to yt-dlp info fields.
    if not geo_restriction_type:
        geo_type_from_info, geo_countries_from_info = _extract_geo_restriction_from_info(info)
        geo_restriction_type = geo_type_from_info
        geo_restriction_countries = geo_countries_from_info

    return YouTubeVideoInfo(
        video_id=video_id,
        title=info.get("title") or api_metadata.get("title") or "",
        description=info.get("description"),
        duration=info.get("duration", 0) or api_metadata.get("duration", 0) or 0,
        channel=info.get("channel") or info.get("uploader") or api_metadata.get("channel"),
        channel_id=info.get("channel_id") or info.get("uploader_id") or api_metadata.get("channel_id"),
        upload_date=info.get("upload_date"),
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        thumbnail=thumbnail or info.get("thumbnail") or api_metadata.get("thumbnail"),
        is_live=info.get("is_live", False) or api_metadata.get("is_live", False) or False,
        categories=info.get("categories"),
        tags=info.get("tags"),
        resolution=resolution,
        geo_restriction_type=geo_restriction_type,
        geo_restriction_countries=geo_restriction_countries or None,
    )


async def analyze_youtube_url(url: str) -> YouTubeVideoInfo:
    """
    Fetch YouTube video metadata from a URL.

    Args:
        url: YouTube URL in any format

    Returns:
        YouTubeVideoInfo object with video metadata

    Raises:
        ValueError: If URL is not a valid YouTube URL
        Exception: If yt-dlp fails to fetch the video info
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {url}")

    return await analyze_youtube_video(video_id)
