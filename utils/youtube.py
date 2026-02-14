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

import yt_dlp

logger = logging.getLogger(__name__)

# Pattern to extract YouTube video ID from various URL formats
YOUTUBE_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})"
)


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
    elif max_height >= 1440:
        return "1440p"
    elif max_height >= 1080:
        return "1080p"
    elif max_height >= 720:
        return "720p"
    elif max_height >= 480:
        return "480p"
    elif max_height > 0:
        return f"{max_height}p"
    return None


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
        return ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=False
        )


async def analyze_youtube_video(video_id: str) -> YouTubeVideoInfo:
    """
    Fetch YouTube video metadata using yt-dlp.

    Args:
        video_id: YouTube video ID (11 character string)

    Returns:
        YouTubeVideoInfo object with video metadata

    Raises:
        ValueError: If video_id is invalid
        Exception: If yt-dlp fails to fetch the video info
    """
    if not video_id or len(video_id) != 11:
        raise ValueError(f"Invalid video ID: {video_id}")

    logger.debug(f"Fetching YouTube video info for: {video_id}")

    # Run yt-dlp in a thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, _fetch_video_info_sync, video_id)

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
            thumbnails, key=lambda x: x.get("preference", 0), reverse=True
        )
        thumbnail = sorted_thumbnails[0].get("url") if sorted_thumbnails else None

    return YouTubeVideoInfo(
        video_id=video_id,
        title=info.get("title", ""),
        description=info.get("description"),
        duration=info.get("duration", 0) or 0,
        channel=info.get("channel") or info.get("uploader"),
        channel_id=info.get("channel_id") or info.get("uploader_id"),
        upload_date=info.get("upload_date"),
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        thumbnail=thumbnail or info.get("thumbnail"),
        is_live=info.get("is_live", False) or False,
        categories=info.get("categories"),
        tags=info.get("tags"),
        resolution=resolution,
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
