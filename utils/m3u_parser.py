"""
M3U playlist parsing utilities for content import.

Provides content type detection (TV/Movie/Series) and title parsing.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class M3UContentType(str, Enum):
    """Content type detected from M3U entry."""

    TV = "tv"
    MOVIE = "movie"
    SERIES = "series"
    UNKNOWN = "unknown"


# Keywords in group-title that indicate content type
TV_KEYWORDS = {
    "live",
    "tv",
    "channel",
    "news",
    "sports",
    "24/7",
    "radio",
    "entertainment",
    "music",
    "kids",
    "documentary",
    "general",
}
MOVIE_KEYWORDS = {
    "movie",
    "movies",
    "film",
    "films",
    "cinema",
    "vod movie",
    "hd movies",
    "4k movies",
}
SERIES_KEYWORDS = {
    "series",
    "shows",
    "tv show",
    "tv shows",
    "episode",
    "season",
    "vod series",
    "drama",
    "sitcom",
}

# URL patterns for content type detection
LIVE_STREAM_EXTENSIONS = {".m3u8", ".ts", ".mpd"}
VOD_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".wmv", ".flv"}

# Series pattern regex - matches various formats:
# S01E02, S1E2, 1x02, Season 1 Episode 2, etc.
SERIES_PATTERNS = [
    # S01E02, S1E2, s01e02
    re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})"),
    # 1x02, 01x02
    re.compile(r"(\d{1,2})[xX](\d{1,3})"),
    # Season 1 Episode 2, Season 01 Episode 02
    re.compile(r"[Ss]eason\s*(\d{1,2})\s*[Ee]pisode\s*(\d{1,3})", re.IGNORECASE),
    # E01, E1 (episode only, assume season 1)
    re.compile(r"\bE(\d{1,3})\b", re.IGNORECASE),
]

# Year pattern in titles - (2023) or [2023] or .2023.
YEAR_PATTERN = re.compile(r"[\(\[\.]?((?:19|20)\d{2})[\)\]\.]?")


@dataclass
class ParsedTitleInfo:
    """Parsed information from title string."""

    clean_title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    is_series: bool = False


def parse_title_info(title: str) -> ParsedTitleInfo:
    """
    Parse title to extract year and series info.

    Args:
        title: The raw title string

    Returns:
        ParsedTitleInfo with extracted data
    """
    clean_title = title.strip()
    year = None
    season = None
    episode = None
    is_series = False

    # Check for series patterns
    for pattern in SERIES_PATTERNS:
        match = pattern.search(clean_title)
        if match:
            is_series = True
            groups = match.groups()
            if len(groups) == 2:
                season = int(groups[0])
                episode = int(groups[1])
            elif len(groups) == 1:
                # Episode only, assume season 1
                season = 1
                episode = int(groups[0])

            # Remove the series info from title for cleaner name
            clean_title = pattern.sub("", clean_title).strip()
            break

    # Extract year
    year_match = YEAR_PATTERN.search(clean_title)
    if year_match:
        year = int(year_match.group(1))
        # Remove year from title
        clean_title = YEAR_PATTERN.sub("", clean_title).strip()

    # Clean up title - remove common separators and extra spaces
    clean_title = re.sub(r"[\.\-_]+", " ", clean_title)
    clean_title = re.sub(r"\s+", " ", clean_title).strip()
    # Remove trailing separators
    clean_title = re.sub(r"[\s\-_\.]+$", "", clean_title)

    return ParsedTitleInfo(
        clean_title=clean_title,
        year=year,
        season=season,
        episode=episode,
        is_series=is_series,
    )


def detect_content_type_from_group(group_title: str) -> M3UContentType | None:
    """
    Detect content type from group-title attribute.

    Args:
        group_title: The group-title attribute value

    Returns:
        Detected content type or None if no clear match
    """
    if not group_title:
        return None

    group_lower = group_title.lower()

    # Check for series keywords first (more specific)
    for keyword in SERIES_KEYWORDS:
        if keyword in group_lower:
            return M3UContentType.SERIES

    # Check for movie keywords
    for keyword in MOVIE_KEYWORDS:
        if keyword in group_lower:
            return M3UContentType.MOVIE

    # Check for TV keywords
    for keyword in TV_KEYWORDS:
        if keyword in group_lower:
            return M3UContentType.TV

    return None


def detect_content_type_from_url(url: str) -> M3UContentType | None:
    """
    Detect content type from URL pattern.

    Args:
        url: The stream URL

    Returns:
        Detected content type or None if no clear match
    """
    if not url:
        return None

    parsed = urlparse(url)
    path_lower = parsed.path.lower()

    # Check for live stream extensions
    for ext in LIVE_STREAM_EXTENSIONS:
        if path_lower.endswith(ext):
            return M3UContentType.TV

    # Check for VOD extensions (could be movie or series)
    for ext in VOD_EXTENSIONS:
        if path_lower.endswith(ext):
            # Can't distinguish movie from series by extension alone
            return M3UContentType.UNKNOWN

    # Check URL path for hints
    if any(x in path_lower for x in ["/live/", "/tv/", "/channel/"]):
        return M3UContentType.TV
    if any(x in path_lower for x in ["/movie/", "/movies/", "/film/"]):
        return M3UContentType.MOVIE
    if any(x in path_lower for x in ["/series/", "/show/", "/episode/"]):
        return M3UContentType.SERIES

    return None


def detect_content_type(
    name: str,
    url: str,
    group_title: str | None = None,
) -> tuple[M3UContentType, ParsedTitleInfo]:
    """
    Detect content type from M3U entry using multiple signals.

    Priority:
    1. Title parsing (series patterns are definitive)
    2. Group-title keywords
    3. URL pattern
    4. Default to UNKNOWN

    Args:
        name: The channel/content name
        url: The stream URL
        group_title: Optional group-title attribute

    Returns:
        Tuple of (detected type, parsed title info)
    """
    # Parse the title first
    title_info = parse_title_info(name)

    # If series pattern found in title, that's definitive
    if title_info.is_series:
        return M3UContentType.SERIES, title_info

    # Check group-title for keywords
    group_type = detect_content_type_from_group(group_title)
    if group_type:
        return group_type, title_info

    # Check URL pattern
    url_type = detect_content_type_from_url(url)
    if url_type and url_type != M3UContentType.UNKNOWN:
        return url_type, title_info

    # If URL indicates VOD but no series info found, likely a movie
    if url_type == M3UContentType.UNKNOWN:
        # Check if URL has VOD extension
        path_lower = urlparse(url).path.lower()
        is_vod = any(path_lower.endswith(ext) for ext in VOD_EXTENSIONS)
        if is_vod:
            return M3UContentType.MOVIE, title_info

    return M3UContentType.UNKNOWN, title_info


def parse_m3u_entry(channel, index: int) -> dict[str, Any]:
    """
    Parse a single M3U entry from ipytv channel object.

    Args:
        channel: ipytv Channel object
        index: Index in the playlist

    Returns:
        Dictionary with parsed channel data
    """
    from ipytv.channel import IPTVAttr

    name = re.sub(r"\s+", " ", channel.name).strip()
    url = channel.url

    # Get attributes
    group_title = channel.attributes.get(IPTVAttr.GROUP_TITLE.value, "")
    country = channel.attributes.get(IPTVAttr.TVG_COUNTRY.value)
    logo = channel.attributes.get(IPTVAttr.TVG_LOGO.value)
    tvg_name = channel.attributes.get(IPTVAttr.TVG_NAME.value, name)
    language = channel.attributes.get(IPTVAttr.TVG_LANGUAGE.value)

    # Parse genres from group-title
    genres = []
    if group_title:
        genres = [re.sub(r"\s+", " ", genre).strip() for genre in re.split(r"[,;|]", group_title) if genre.strip()]

    # Detect content type
    detected_type, title_info = detect_content_type(name, url, group_title)

    return {
        "index": index,
        "name": name,
        "url": url,
        "logo": logo if logo and logo.startswith("http") else None,
        "genres": genres,
        "country": country,
        "language": language,
        "tvg_name": tvg_name,
        "detected_type": detected_type.value,
        "parsed_title": title_info.clean_title,
        "parsed_year": title_info.year,
        "season": title_info.season,
        "episode": title_info.episode,
    }


async def parse_m3u_playlist_for_preview(
    playlist_content: str | None = None,
    playlist_url: str | None = None,
    preview_limit: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """
    Parse M3U playlist and return preview data.

    Args:
        playlist_content: M3U content as string
        playlist_url: URL to fetch M3U from
        preview_limit: Max entries to return in preview

    Returns:
        Tuple of (preview entries, type summary, total count)
    """
    from ipytv import playlist

    if playlist_content:
        iptv_playlist = playlist.loads(playlist_content)
    elif playlist_url:
        iptv_playlist = playlist.loadu(playlist_url)
    else:
        raise ValueError("Either playlist_content or playlist_url must be provided")

    entries = []
    summary = {
        M3UContentType.TV.value: 0,
        M3UContentType.MOVIE.value: 0,
        M3UContentType.SERIES.value: 0,
        M3UContentType.UNKNOWN.value: 0,
    }

    total_count = 0
    for idx, channel in enumerate(iptv_playlist):
        total_count += 1

        try:
            entry = parse_m3u_entry(channel, idx)
            summary[entry["detected_type"]] += 1

            # Only include in preview up to limit
            if len(entries) < preview_limit:
                entries.append(entry)
        except Exception as e:
            logger.warning(f"Failed to parse M3U entry {idx}: {e}")
            summary[M3UContentType.UNKNOWN.value] += 1

    return entries, summary, total_count
