"""Stream processing service."""

import logging
from collections.abc import Sequence
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from db import crud
from db.models import Stream, StreamType, TorrentStream
from db.schemas import UserData

from .base import BaseService


class StreamService(BaseService):
    """Service for stream processing operations.

    Handles stream filtering, sorting, and formatting for different
    streaming providers and user preferences.
    """

    def __init__(
        self,
        session: AsyncSession,
        user_data: UserData | None = None,
        logger: logging.Logger | None = None,
    ):
        """Initialize the stream service."""
        super().__init__(session=session, logger=logger)
        self._user_data = user_data

    @property
    def user_data(self) -> UserData | None:
        """Get user data configuration."""
        return self._user_data

    def with_user_data(self, user_data: UserData) -> "StreamService":
        """Create a new service instance with user data."""
        return StreamService(
            session=self._session,
            user_data=user_data,
            logger=self._logger,
        )

    async def get_streams(
        self,
        media_id: int,
        *,
        stream_type: StreamType | None = None,
        only_working: bool = True,
        limit: int = 100,
    ) -> Sequence[Stream]:
        """Get streams for a media entry."""
        return await crud.get_streams_for_media(
            self._session,
            media_id,
            stream_type=stream_type,
            only_working=only_working,
            limit=limit,
        )

    async def get_movie_streams(
        self,
        media_id: int,
    ) -> list[dict[str, Any]]:
        """Get formatted streams for a movie."""
        streams = await self.get_streams(media_id)
        return await self._format_streams(streams)

    async def get_series_streams(
        self,
        media_id: int,
        season: int,
        episode: int,
    ) -> list[dict[str, Any]]:
        """Get formatted streams for a series episode."""
        # Get files linked to this specific episode
        episode_files = await crud.get_files_for_episode(
            self._session, media_id=media_id, season=season, episode=episode
        )

        # Get unique streams from these files
        stream_ids = set()
        episode_streams = []
        for file_obj in episode_files:
            if file_obj.stream_id not in stream_ids:
                stream_ids.add(file_obj.stream_id)
                # Get the full stream with relationships
                stream = await crud.get_stream_by_id(
                    self._session,
                    file_obj.stream_id,
                    load_files=True,
                    load_trackers=True,
                )
                if stream:
                    episode_streams.append(stream)

        return await self._format_streams(episode_streams)

    async def get_torrent_stream(
        self,
        info_hash: str,
    ) -> TorrentStream | None:
        """Get a torrent stream by info hash."""
        return await crud.get_torrent_by_info_hash(
            self._session,
            info_hash,
            load_files=True,
            load_trackers=True,
        )

    async def vote(
        self,
        user_id: int,
        stream_id: int,
        vote: int,  # 1 or -1
    ) -> None:
        """Vote on a stream quality."""
        await crud.vote_on_stream(self._session, user_id, stream_id, vote)

    async def get_vote_stats(
        self,
        stream_id: int,
    ) -> dict[str, int]:
        """Get vote statistics for a stream."""
        return await crud.get_stream_vote_count(self._session, stream_id)

    async def track_play(
        self,
        stream_id: int,
        media_id: int,
        *,
        user_id: int | None = None,
        season: int | None = None,
        episode: int | None = None,
        provider_name: str | None = None,
    ) -> None:
        """Track a playback event."""
        await crud.track_playback(
            self._session,
            stream_id=stream_id,
            media_id=media_id,
            user_id=user_id,
            season=season,
            episode=episode,
            provider_name=provider_name,
        )

    async def _format_streams(
        self,
        streams: Sequence[Stream],
    ) -> list[dict[str, Any]]:
        """Format streams for API response with normalized quality attributes."""
        formatted = []
        for stream in streams:
            stream_dict = {
                "id": stream.id,
                "name": stream.name,
                "type": stream.stream_type.value,
                "source": stream.source,
                # Single-value quality attributes
                "resolution": stream.resolution,
                "codec": stream.codec,
                "quality": stream.quality,
                "bit_depth": stream.bit_depth,
                "uploader": stream.uploader,
                # Multi-value quality attributes (from normalized tables)
                "audio_formats": [af.name for af in stream.audio_formats] if stream.audio_formats else [],
                "channels": [ch.name for ch in stream.channels] if stream.channels else [],
                "hdr_formats": [hf.name for hf in stream.hdr_formats] if stream.hdr_formats else [],
                "languages": [lang.name for lang in stream.languages] if stream.languages else [],
                # Boolean flags
                "is_remastered": stream.is_remastered,
                "is_upscaled": stream.is_upscaled,
                "is_proper": stream.is_proper,
                "is_repack": stream.is_repack,
                "is_extended": stream.is_extended,
                "is_complete": stream.is_complete,
                "is_dubbed": stream.is_dubbed,
                "is_subbed": stream.is_subbed,
                # Aggregates
                "vote_score": stream.vote_score,
                "playback_count": stream.playback_count,
                "created_at": stream.created_at.isoformat() if stream.created_at else None,
            }

            # Add type-specific data for torrents
            if stream.stream_type == StreamType.TORRENT and hasattr(stream, "torrent_stream"):
                torrent = stream.torrent_stream
                if torrent:
                    stream_dict["info_hash"] = torrent.info_hash
                    stream_dict["seeders"] = torrent.seeders
                    stream_dict["size"] = torrent.total_size

            formatted.append(stream_dict)

        return formatted

    def filter_by_resolution(
        self,
        streams: list[dict[str, Any]],
        resolutions: list[str],
    ) -> list[dict[str, Any]]:
        """Filter streams by resolution."""
        if not resolutions:
            return streams

        resolution_set = {r.lower() for r in resolutions}
        return [s for s in streams if s.get("resolution", "").lower() in resolution_set]

    def filter_by_quality(
        self,
        streams: list[dict[str, Any]],
        qualities: list[str],
    ) -> list[dict[str, Any]]:
        """Filter streams by quality."""
        if not qualities:
            return streams

        quality_set = {q.lower() for q in qualities}
        return [s for s in streams if s.get("quality", "").lower() in quality_set]

    def filter_by_codec(
        self,
        streams: list[dict[str, Any]],
        codecs: list[str],
    ) -> list[dict[str, Any]]:
        """Filter streams by codec."""
        if not codecs:
            return streams

        codec_set = {c.lower() for c in codecs}
        return [s for s in streams if s.get("codec", "").lower() in codec_set]

    def sort_streams(
        self,
        streams: list[dict[str, Any]],
        sorting_priority: list[str],
    ) -> list[dict[str, Any]]:
        """Sort streams based on user preferences."""
        if not sorting_priority:
            return streams

        def sort_key(stream: dict[str, Any]) -> tuple:
            return tuple(self._get_sort_value(stream, criterion) for criterion in sorting_priority)

        return sorted(streams, key=sort_key, reverse=True)

    def _get_sort_value(
        self,
        stream: dict[str, Any],
        criterion: str,
    ) -> Any:
        """Get the sort value for a stream based on criterion."""
        criteria_map = {
            "resolution": lambda s: self._resolution_rank(s.get("resolution", "")),
            "seeders": lambda s: s.get("seeders", 0),
            "size": lambda s: s.get("size", 0),
            "quality": lambda s: self._quality_rank(s.get("quality", "")),
            "vote_score": lambda s: s.get("vote_score", 0),
            "playback_count": lambda s: s.get("playback_count", 0),
        }

        getter = criteria_map.get(criterion, lambda s: 0)
        return getter(stream)

    def _resolution_rank(self, resolution: str) -> int:
        """Get numeric rank for resolution."""
        resolution_ranks = {
            "4k": 5,
            "2160p": 5,
            "1080p": 4,
            "720p": 3,
            "480p": 2,
            "360p": 1,
        }
        return resolution_ranks.get(resolution.lower(), 0)

    def _quality_rank(self, quality: str) -> int:
        """Get numeric rank for quality."""
        quality_ranks = {
            "remux": 6,
            "bluray": 5,
            "web-dl": 4,
            "webrip": 3,
            "hdtv": 2,
            "cam": 1,
        }
        return quality_ranks.get(quality.lower(), 0)
