"""Easynews utility functions for Usenet streaming."""

import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from db.schemas import StreamingProvider
from db.schemas.media import UsenetStreamData
from streaming_providers.easynews.client import Easynews
from streaming_providers.exceptions import ProviderException


@asynccontextmanager
async def initialize_easynews(streaming_provider: StreamingProvider) -> AsyncGenerator[Easynews, Any]:
    """Initialize Easynews client from streaming provider config.

    Args:
        streaming_provider: Provider configuration

    Yields:
        Initialized Easynews client
    """
    config = streaming_provider.easynews_config
    if not config:
        raise ProviderException("Easynews configuration not found", "invalid_credentials.mp4")

    async with Easynews(username=config.username, password=config.password) as client:
        yield client


def select_best_result(
    results: list[dict],
    filename: str | None,
    season: int | None,
    episode: int | None,
    preferred_resolution: str | None = None,
) -> dict | None:
    """Select the best result from search results.

    Args:
        results: List of search results
        filename: Target filename
        season: Season number for series
        episode: Episode number for series
        preferred_resolution: Preferred resolution (e.g., "1080p", "4k")

    Returns:
        Best matching result or None
    """
    if not results:
        return None

    video_extensions = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".webm"}

    # Filter to video files only
    video_results = []
    for r in results:
        ext = r.get("extension", "").lower()
        fname = r.get("filename", "").lower()
        if ext in video_extensions or any(fname.endswith(e) for e in video_extensions):
            video_results.append(r)

    if not video_results:
        video_results = results

    # If filename is provided, try to match it
    if filename:
        filename_lower = filename.lower()
        for r in video_results:
            if r.get("filename", "").lower() == filename_lower:
                return r

    # For series, try to match season/episode
    if season is not None and episode is not None:
        pattern = rf"[sS]{season:02d}[eE]{episode:02d}"
        matching = []
        for r in video_results:
            fname = r.get("filename", "") or r.get("subject", "")
            if re.search(pattern, fname):
                matching.append(r)
        if matching:
            video_results = matching

    # Score results based on quality
    def score_result(r: dict) -> int:
        score = 0
        resolution = r.get("resolution", "")

        # Resolution scoring
        if "2160" in resolution or "4k" in resolution.lower():
            score += 100
        elif "1080" in resolution:
            score += 80
        elif "720" in resolution:
            score += 60
        elif "480" in resolution:
            score += 40

        # Prefer larger files (usually better quality)
        size = r.get("size", 0)
        if size > 5 * 1024 * 1024 * 1024:  # > 5GB
            score += 30
        elif size > 2 * 1024 * 1024 * 1024:  # > 2GB
            score += 20
        elif size > 1 * 1024 * 1024 * 1024:  # > 1GB
            score += 10

        # Codec preference
        codec = r.get("codec", "").lower()
        if "x265" in codec or "hevc" in codec:
            score += 15
        elif "x264" in codec or "h264" in codec:
            score += 10

        # Preferred resolution bonus
        if preferred_resolution:
            if preferred_resolution.lower() in resolution.lower():
                score += 50

        return score

    # Sort by score and return best
    video_results.sort(key=score_result, reverse=True)
    return video_results[0] if video_results else None


async def get_video_url_from_easynews(
    nzb_hash: str,
    streaming_provider: StreamingProvider,
    filename: str | None,
    stream: UsenetStreamData,
    season: int | None = None,
    episode: int | None = None,
    **kwargs: Any,
) -> str:
    """Get video URL from Easynews for Usenet content.

    Easynews provides direct streaming URLs without requiring download.

    Args:
        nzb_hash: Hash of the NZB content (used as search fallback)
        streaming_provider: Provider configuration
        filename: Target filename
        stream: Usenet stream data
        season: Season number for series
        episode: Episode number for series

    Returns:
        Direct streaming URL
    """
    async with initialize_easynews(streaming_provider) as easynews:
        # Try to find by filename first
        search_query = stream.name

        results = await easynews.search(search_query, max_results=50)

        if not results:
            # Try searching by NZB title without year/quality info
            # Extract base title
            base_title = re.sub(r"[\.\-_]", " ", stream.name)
            base_title = re.sub(r"\s+", " ", base_title).strip()
            results = await easynews.search(base_title, max_results=50)

        if not results:
            raise ProviderException("No results found on Easynews", "no_video_file_found.mp4")

        # Select the best result
        best_result = select_best_result(results, filename, season, episode)

        if not best_result:
            raise ProviderException("No matching video found on Easynews", "no_video_file_found.mp4")

        # Generate the download/streaming URL
        return easynews.generate_download_url(
            file_id=best_result["id"],
            filename=best_result["filename"],
            sig=best_result.get("sig"),
        )


async def search_easynews_for_movie(
    streaming_provider: StreamingProvider,
    title: str,
    year: int | None = None,
    imdb_id: str | None = None,
) -> list[dict]:
    """Search Easynews for a movie.

    Args:
        streaming_provider: Provider configuration
        title: Movie title
        year: Release year
        imdb_id: IMDb ID

    Returns:
        List of search results
    """
    async with initialize_easynews(streaming_provider) as easynews:
        return await easynews.search_movie(title, year, imdb_id)


async def search_easynews_for_episode(
    streaming_provider: StreamingProvider,
    title: str,
    season: int,
    episode: int,
    imdb_id: str | None = None,
) -> list[dict]:
    """Search Easynews for a TV episode.

    Args:
        streaming_provider: Provider configuration
        title: Series title
        season: Season number
        episode: Episode number
        imdb_id: IMDb ID

    Returns:
        List of search results
    """
    async with initialize_easynews(streaming_provider) as easynews:
        return await easynews.search_episode(title, season, episode, imdb_id)


async def update_easynews_cache_status(
    streams: list[UsenetStreamData], streaming_provider: StreamingProvider, **kwargs: Any
) -> None:
    """Update cache status for usenet streams based on Easynews availability.

    Easynews provides instant streaming, so we search for each stream to check availability.
    This is more expensive than debrid cache checks, so we mark all as potentially available.

    Args:
        streams: List of usenet streams to check
        streaming_provider: Provider configuration
    """
    # Easynews is an instant streaming service - content is always "cached"
    # if it exists on their servers. We can't efficiently check each stream,
    # so we mark all as potentially available and let playback handle failures.
    for stream in streams:
        stream.cached = True  # Optimistic - Easynews has vast content


async def fetch_downloaded_usenet_hashes_from_easynews(
    streaming_provider: StreamingProvider, **kwargs: Any
) -> list[str]:
    """Fetch hashes from Easynews.

    Easynews doesn't store user downloads, so this returns empty.

    Args:
        streaming_provider: Provider configuration

    Returns:
        Empty list (Easynews is streaming-only)
    """
    return []


async def delete_all_usenet_from_easynews(streaming_provider: StreamingProvider, **kwargs: Any) -> None:
    """Delete all from Easynews.

    Easynews is streaming-only, no downloads to delete.

    Args:
        streaming_provider: Provider configuration
    """
    pass  # No-op for Easynews


async def delete_usenet_from_easynews(streaming_provider: StreamingProvider, nzb_hash: str, **kwargs: Any) -> bool:
    """Delete from Easynews.

    Easynews is streaming-only, no downloads to delete.

    Args:
        streaming_provider: Provider configuration
        nzb_hash: NZB hash (unused)

    Returns:
        True (no-op success)
    """
    return True  # No-op for Easynews


async def validate_easynews_credentials(streaming_provider: StreamingProvider, **kwargs: Any) -> dict[str, str]:
    """Validate Easynews credentials.

    Args:
        streaming_provider: Provider configuration

    Returns:
        Status dict with success or error message
    """
    try:
        async with initialize_easynews(streaming_provider) as easynews:
            is_valid = await easynews.verify_credentials()
            if is_valid:
                return {"status": "success"}
            else:
                return {
                    "status": "error",
                    "message": "Invalid Easynews credentials",
                }
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate Easynews credentials: {error.message}",
        }
