"""
Torznab API CRUD operations.

Database queries for searching torrents to serve via Torznab API.
"""

import logging
from typing import Literal

from sqlalchemy import and_, or_
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.enums import MediaType
from db.models import (
    FileMediaLink,
    Media,
    MediaExternalID,
    Stream,
    StreamFile,
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


def _apply_episode_filter(id_q, season: int | None, episode: int | None):
    """Restrict an id-subquery to streams containing a specific season/episode.

    Joins StreamFile + FileMediaLink (indexed by (media_id, season_number, episode_number))
    and matches either an exact episode_number or an episode range (episode_number..episode_end).
    """
    if season is None and episode is None:
        return id_q

    id_q = id_q.join(StreamFile, StreamFile.stream_id == Stream.id).join(
        FileMediaLink,
        and_(
            FileMediaLink.file_id == StreamFile.id,
            FileMediaLink.media_id == Media.id,
        ),
    )

    if season is not None:
        id_q = id_q.where(FileMediaLink.season_number == season)

    if episode is not None:
        id_q = id_q.where(
            or_(
                FileMediaLink.episode_number == episode,
                and_(
                    FileMediaLink.episode_end.is_not(None),
                    FileMediaLink.episode_number <= episode,
                    FileMediaLink.episode_end >= episode,
                ),
            )
        )

    return id_q


async def _load_torrent_results(session: AsyncSession, id_subquery) -> list[dict]:
    """Materialize full (TorrentStream, Stream, Media) rows for the deduplicated ids
    and build the Torznab result dicts.

    The sort on DISTINCT ON happens here over a tiny bounded set (≤ limit rows, x few
    stream_media_link rows for multi-media torrents), so the sort stays in memory.
    """
    query = (
        select(TorrentStream, Stream, Media)
        .join(Stream, Stream.id == TorrentStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .join(Media, Media.id == StreamMediaLink.media_id)
        .where(TorrentStream.id.in_(id_subquery.scalar_subquery()))
        .options(selectinload(TorrentStream.trackers))
        .distinct(TorrentStream.id)
    )

    result = await session.exec(query)
    rows = result.all()
    if not rows:
        return []

    # Batch-load external IDs for all media in one query instead of N+1 per row.
    media_ids = {media.id for _, _, media in rows}
    ext_query = select(MediaExternalID).where(MediaExternalID.media_id.in_(media_ids))
    ext_result = await session.exec(ext_query)
    external_ids_by_media: dict[int, dict[str, str]] = {}
    for ext in ext_result.all():
        external_ids_by_media.setdefault(ext.media_id, {})[ext.provider] = ext.external_id

    results = []
    for torrent, stream, media in rows:
        external_ids = external_ids_by_media.get(media.id, {})
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
    # Step 1: narrow id-only subquery. Sorting just the 4-byte id means the
    # DISTINCT sort fits in memory instead of spilling to pgsql_tmp/.
    id_q = (
        select(TorrentStream.id)
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
    )

    if media_type == "movie":
        id_q = id_q.where(Media.type == MediaType.MOVIE)
    elif media_type == "series":
        id_q = id_q.where(Media.type == MediaType.SERIES)

    id_q = _apply_episode_filter(id_q, season, episode)
    id_q = id_q.distinct().limit(limit)

    return await _load_torrent_results(session, id_q)


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

    id_q = (
        select(TorrentStream.id)
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
    )

    if media_type == "movie":
        id_q = id_q.where(Media.type == MediaType.MOVIE)
    elif media_type == "series":
        id_q = id_q.where(Media.type == MediaType.SERIES)

    if year:
        id_q = id_q.where(Media.year == year)

    id_q = id_q.distinct().limit(limit)

    return await _load_torrent_results(session, id_q)


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
    id_q = (
        select(TorrentStream.id)
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
    )

    if media_type == "movie":
        id_q = id_q.where(Media.type == MediaType.MOVIE)
    elif media_type == "series":
        id_q = id_q.where(Media.type == MediaType.SERIES)

    id_q = _apply_episode_filter(id_q, season, episode)
    id_q = id_q.distinct().limit(limit)

    return await _load_torrent_results(session, id_q)
