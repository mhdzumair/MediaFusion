"""
Torznab API CRUD operations.

Database queries for searching torrents to serve via Torznab API.
"""

import logging
from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.enums import MediaType
from db.models import (
    Media,
    MediaExternalID,
    Stream,
    StreamMediaLink,
    TorrentStream,
)

logger = logging.getLogger(__name__)


# Resolution to Torznab category mapping
RESOLUTION_TO_CATEGORY = {
    # Movies
    "movie": {
        "4k": 2045,  # UHD
        "2160p": 2045,
        "1080p": 2040,  # HD
        "720p": 2040,
        "480p": 2030,  # SD
        "default": 2000,  # General
    },
    # Series
    "series": {
        "4k": 5045,  # UHD
        "2160p": 5045,
        "1080p": 5030,  # HD
        "720p": 5030,
        "480p": 5020,  # SD
        "default": 5000,  # General
    },
}


def get_category_for_stream(media_type: MediaType, resolution: str | None) -> int:
    """Get Torznab category based on media type and resolution."""
    type_key = "movie" if media_type == MediaType.MOVIE else "series"
    categories = RESOLUTION_TO_CATEGORY.get(type_key, RESOLUTION_TO_CATEGORY["movie"])

    if resolution:
        res_lower = resolution.lower()
        for key, cat in categories.items():
            if key in res_lower:
                return cat

    return categories["default"]


async def search_torrents_by_imdb(
    session: AsyncSession,
    imdb_id: str,
    media_type: Literal["movie", "series"] | None = None,
    season: int | None = None,
    episode: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Search torrents by IMDb ID.

    Returns a list of dicts with torrent info for Torznab XML generation.
    """
    # Build base query joining through the relationships
    query = (
        select(
            TorrentStream,
            Stream,
            Media,
        )
        .join(Stream, Stream.id == TorrentStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .join(Media, Media.id == StreamMediaLink.media_id)
        .join(MediaExternalID, MediaExternalID.media_id == Media.id)
        .where(
            MediaExternalID.provider == "imdb",
            MediaExternalID.external_id == imdb_id,
            Stream.is_active.is_(True),
            Stream.is_blocked.is_(False),
            Stream.is_public.is_(True),
        )
        .options(
            selectinload(TorrentStream.trackers),
        )
        .distinct(TorrentStream.id)
        .limit(limit)
    )

    # Filter by media type if specified
    if media_type == "movie":
        query = query.where(Media.type == MediaType.MOVIE)
    elif media_type == "series":
        query = query.where(Media.type == MediaType.SERIES)

    result = await session.exec(query)
    rows = result.all()

    # Build result list
    results = []
    for torrent, stream, media in rows:
        # Get external IDs for this media
        ext_query = select(MediaExternalID).where(MediaExternalID.media_id == media.id)
        ext_result = await session.exec(ext_query)
        external_ids = {row.provider: row.external_id for row in ext_result.all()}

        # Build tracker list for magnet
        trackers = [t.url for t in torrent.trackers] if torrent.trackers else []

        results.append(
            {
                "info_hash": torrent.info_hash,
                "name": stream.name,
                "size": torrent.total_size,
                "seeders": torrent.seeders,
                "leechers": torrent.leechers,
                "uploaded_at": torrent.uploaded_at or stream.created_at,
                "resolution": stream.resolution,
                "media_type": media.type,
                "media_title": media.title,
                "media_year": media.year,
                "imdb_id": external_ids.get("imdb"),
                "tmdb_id": external_ids.get("tmdb"),
                "trackers": trackers,
                "source": stream.source,
            }
        )

    return results


async def search_torrents_by_title(
    session: AsyncSession,
    query_text: str,
    media_type: Literal["movie", "series"] | None = None,
    year: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Search torrents by title text.

    Uses trigram similarity for fuzzy matching.
    """
    search_pattern = f"%{query_text}%"

    # Build base query
    query = (
        select(
            TorrentStream,
            Stream,
            Media,
        )
        .join(Stream, Stream.id == TorrentStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .join(Media, Media.id == StreamMediaLink.media_id)
        .where(
            Stream.is_active.is_(True),
            Stream.is_blocked.is_(False),
            Stream.is_public.is_(True),
            or_(
                Media.title.ilike(search_pattern),
                Stream.name.ilike(search_pattern),
            ),
        )
        .options(
            selectinload(TorrentStream.trackers),
        )
        .distinct(TorrentStream.id)
        .limit(limit)
    )

    # Filter by media type
    if media_type == "movie":
        query = query.where(Media.type == MediaType.MOVIE)
    elif media_type == "series":
        query = query.where(Media.type == MediaType.SERIES)

    # Filter by year
    if year:
        query = query.where(Media.year == year)

    result = await session.exec(query)
    rows = result.all()

    # Build result list
    results = []
    for torrent, stream, media in rows:
        # Get external IDs for this media
        ext_query = select(MediaExternalID).where(MediaExternalID.media_id == media.id)
        ext_result = await session.exec(ext_query)
        external_ids = {row.provider: row.external_id for row in ext_result.all()}

        # Build tracker list for magnet
        trackers = [t.url for t in torrent.trackers] if torrent.trackers else []

        results.append(
            {
                "info_hash": torrent.info_hash,
                "name": stream.name,
                "size": torrent.total_size,
                "seeders": torrent.seeders,
                "leechers": torrent.leechers,
                "uploaded_at": torrent.uploaded_at or stream.created_at,
                "resolution": stream.resolution,
                "media_type": media.type,
                "media_title": media.title,
                "media_year": media.year,
                "imdb_id": external_ids.get("imdb"),
                "tmdb_id": external_ids.get("tmdb"),
                "trackers": trackers,
                "source": stream.source,
            }
        )

    return results


async def search_torrents_by_tmdb(
    session: AsyncSession,
    tmdb_id: str,
    media_type: Literal["movie", "series"] | None = None,
    season: int | None = None,
    episode: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Search torrents by TMDB ID.
    """
    # Build base query
    query = (
        select(
            TorrentStream,
            Stream,
            Media,
        )
        .join(Stream, Stream.id == TorrentStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .join(Media, Media.id == StreamMediaLink.media_id)
        .join(MediaExternalID, MediaExternalID.media_id == Media.id)
        .where(
            MediaExternalID.provider == "tmdb",
            MediaExternalID.external_id == tmdb_id,
            Stream.is_active.is_(True),
            Stream.is_blocked.is_(False),
            Stream.is_public.is_(True),
        )
        .options(
            selectinload(TorrentStream.trackers),
        )
        .distinct(TorrentStream.id)
        .limit(limit)
    )

    # Filter by media type
    if media_type == "movie":
        query = query.where(Media.type == MediaType.MOVIE)
    elif media_type == "series":
        query = query.where(Media.type == MediaType.SERIES)

    result = await session.exec(query)
    rows = result.all()

    # Build result list
    results = []
    for torrent, stream, media in rows:
        # Get external IDs for this media
        ext_query = select(MediaExternalID).where(MediaExternalID.media_id == media.id)
        ext_result = await session.exec(ext_query)
        external_ids = {row.provider: row.external_id for row in ext_result.all()}

        # Build tracker list for magnet
        trackers = [t.url for t in torrent.trackers] if torrent.trackers else []

        results.append(
            {
                "info_hash": torrent.info_hash,
                "name": stream.name,
                "size": torrent.total_size,
                "seeders": torrent.seeders,
                "leechers": torrent.leechers,
                "uploaded_at": torrent.uploaded_at or stream.created_at,
                "resolution": stream.resolution,
                "media_type": media.type,
                "media_title": media.title,
                "media_year": media.year,
                "imdb_id": external_ids.get("imdb"),
                "tmdb_id": external_ids.get("tmdb"),
                "trackers": trackers,
                "source": stream.source,
            }
        )

    return results
