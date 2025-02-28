import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from os.path import basename
from typing import Any, Optional, TypedDict

import PTT

from db.models import TorrentStreams, EpisodeFile, KnownFile
from streaming_providers.exceptions import ProviderException
from utils.telegram_bot import telegram_notifier
from utils.validation_helper import is_video_file

logger = logging.getLogger(__name__)


class TorrentFile(TypedDict):
    name: str
    size: int


@dataclass
class FileInfo:
    index: int
    filename: str
    size: int
    season: Optional[int] = None
    episode: Optional[int] = None
    is_video: bool = False

    @classmethod
    def from_torrent_file(
        cls,
        index: int,
        file_data: dict[str, Any],
        name_key: str = "name",
        size_key: str = "size",
    ) -> "FileInfo":
        filename = basename(file_data[name_key])
        return cls(
            index=index,
            filename=filename,
            size=file_data[size_key],
            is_video=is_video_file(filename),
        )


class TorrentFileProcessor:
    def __init__(
        self,
        torrent_info: dict[str, Any],
        file_key: str = "files",
        name_key: str = "name",
        size_key: str = "size",
    ):
        self.torrent_info = torrent_info
        self.file_key = file_key
        self.name_key = name_key
        self.size_key = size_key
        self.files = torrent_info[file_key]
        self.file_infos = self._process_files()
        self._video_files: Optional[list[FileInfo]] = None
        self._episodes: Optional[list[EpisodeFile]] = None

    def _process_files(self) -> list[FileInfo]:
        """Process all files in the torrent and create FileInfo objects."""
        file_infos = []
        for idx, file in enumerate(self.files):
            file_info = FileInfo.from_torrent_file(
                idx, file, self.name_key, self.size_key
            )
            if file_info.is_video:
                # Don't parse season/episode info here - do it only when needed
                file_infos.append(file_info)

        return file_infos

    def get_video_files(self) -> list[FileInfo]:
        """Get all video files from the torrent, caching the result."""
        if self._video_files is None:
            self._video_files = [f for f in self.file_infos if f.is_video]
        return self._video_files

    def get_largest_video_file(self) -> Optional[FileInfo]:
        """Get the largest video file from the torrent."""
        video_files = self.get_video_files()
        return max(video_files, key=lambda x: x.size) if video_files else None

    def _parse_season_episode_info(
        self,
        file_info: FileInfo,
        torrent_title: Optional[str] = None,
        default_season: Optional[int] = None,
    ) -> tuple[Optional[int], Optional[int]]:
        """Parse season and episode information from filename and torrent title."""
        # First try from filename
        parsed_data = PTT.parse_title(file_info.filename)
        seasons = parsed_data.get("seasons", [])
        episodes = parsed_data.get("episodes", [])

        season = seasons[0] if seasons else None
        episode = episodes[0] if episodes else None

        # If no season/episode found, try from torrent title
        if torrent_title and (not season or not episode):
            title_parsed = PTT.parse_title(torrent_title)
            title_seasons = title_parsed.get("seasons", [])
            title_episodes = title_parsed.get("episodes", [])

            # If only one season in title, use it
            if len(title_seasons) == 1:
                season = season or title_seasons[0]

            # If only one episode in title, use it
            if len(title_episodes) == 1:
                episode = episode or title_episodes[0]

        # For season packs without explicit season number
        if not season and episode:
            season = default_season or 1

        return season, episode

    def find_specific_episode(
        self, season: int, episode: int, torrent_title: Optional[str] = None
    ) -> Optional[FileInfo]:
        """Find a specific episode in the video files."""
        if self._episodes:
            for episode_file in self._episodes:
                if (
                    episode_file.season_number == season
                    and episode_file.episode_number == episode
                ):
                    return self.file_infos[episode_file.file_index]

        for file_info in self.get_video_files():
            parsed_season, parsed_episode = self._parse_season_episode_info(
                file_info, torrent_title, default_season=season
            )
            if parsed_season == season and parsed_episode == episode:
                return file_info
        return None

    def parse_all_episodes(
        self, torrent_title: str, default_season: Optional[int] = None
    ) -> list[EpisodeFile]:
        """Parse all episode information from video files."""
        episodes = []
        for file_info in self.get_video_files():
            season, episode = self._parse_season_episode_info(
                file_info, torrent_title, default_season
            )
            if season and episode:
                episodes.append(
                    EpisodeFile(
                        season_number=season,
                        episode_number=episode,
                        filename=file_info.filename,
                        size=file_info.size,
                        file_index=file_info.index,
                    )
                )
        self._episodes = episodes
        return episodes

    def find_file_by_name(self, filename: str) -> Optional[FileInfo]:
        """Find a file by its filename."""
        for file_info in self.file_infos:
            if file_info.filename == filename:
                return file_info
        return None


async def select_file_index_from_torrent(
    torrent_info: dict[str, Any],
    torrent_stream: TorrentStreams,
    filename: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    file_key: str = "files",
    name_key: str = "name",
    size_key: str = "size",
    file_size_callback: Optional[callable] = None,
    is_filename_trustable: bool = False,
    is_index_trustable: bool = False,
) -> int:
    """
    Select the file index from the torrent info with minimal processing.
    Only processes all files if initial filename match fails.
    """
    files = torrent_info[file_key]

    # Quick filename match without full processing
    if filename:
        for idx, file in enumerate(files):
            file_name = basename(file[name_key])
            if file_name == filename:
                return idx

    # Initialize processor for further processing
    # get file sizes if callback provided
    if file_size_callback:
        await file_size_callback(files)
    processor = TorrentFileProcessor(torrent_info, file_key, name_key, size_key)

    # Check if there are any video files
    video_files = processor.get_video_files()
    if not video_files:
        if is_filename_trustable:
            torrent_stream.is_blocked = True
            await torrent_stream.save()
            raise ProviderException(
                "No valid video files found in torrent. Torrent has been blocked.",
                "no_video_file.mp4",
            )
        else:
            raise ProviderException(
                "No valid video files found in torrent",
                "no_video_file.mp4",
            )

    # Update metadata if filename is trustable
    if is_filename_trustable:
        await update_torrent_streams_metadata(
            torrent_stream=torrent_stream,
            processor=processor,
            season=season,
            is_index_trustable=is_index_trustable,
            is_filename_trustable=is_filename_trustable,
        )

    # Try to find by season/episode
    if season and episode:
        selected_file = processor.find_specific_episode(
            season, episode, torrent_stream.torrent_name
        )
        if selected_file:
            return selected_file.index
        else:
            # Found video files but couldn't match season/episode
            await _request_annotation(torrent_stream, processor.file_infos)
            raise ProviderException(
                "Found video files but couldn't match season/episode. "
                "Annotation has been requested.",
                "episode_not_found.mp4",
            )

    # Default to largest video file
    selected_file = processor.get_largest_video_file()
    if selected_file:
        return selected_file.index

    raise ProviderException(
        "No valid video file found in torrent",
        "no_video_file.mp4",
    )


async def update_torrent_streams_metadata(
    torrent_stream: TorrentStreams,
    processor: TorrentFileProcessor,
    season: Optional[int] = None,
    is_index_trustable: bool = False,
    is_filename_trustable: bool = False,
) -> bool:
    """
    Update torrent stream metadata using the provided processor instance.
    Returns True if update was successful, False if annotation was requested.
    """
    # Check if annotation was recently requested
    if torrent_stream.annotation_requested_at and datetime.now(
        tz=timezone.utc
    ) - torrent_stream.annotation_requested_at < timedelta(days=7):
        logger.info(
            f"Skipping metadata update for {torrent_stream.id} - "
            "recent annotation request"
        )
        return False

    try:
        if season is None:
            # Movie handling
            file_info = processor.get_largest_video_file()
            if not file_info:
                if is_filename_trustable:
                    torrent_stream.is_blocked = True
                    await torrent_stream.save()
                    logger.error(f"No video files found in torrent {torrent_stream.id}")
                    return False
                await _request_annotation(torrent_stream, processor.file_infos)
                return False

            torrent_stream.filename = file_info.filename
            if is_index_trustable:
                torrent_stream.file_index = file_info.index
            torrent_stream.size = file_info.size

        else:
            # Series handling
            video_files = processor.get_video_files()
            if not video_files:
                if is_filename_trustable:
                    torrent_stream.is_blocked = True
                    await torrent_stream.save()
                    logger.error(f"No video files found in torrent {torrent_stream.id}")
                    return False
                await _request_annotation(torrent_stream, processor.file_infos)
                return False

            episodes = processor.parse_all_episodes(
                torrent_stream.torrent_name, default_season=season
            )

            if not episodes:
                await _request_annotation(torrent_stream, processor.file_infos)
                return False

            torrent_stream.episode_files = episodes
            total_size = sum(f.size for f in processor.get_video_files())
            if total_size > torrent_stream.size:
                torrent_stream.size = total_size

        torrent_stream.updated_at = datetime.now(tz=timezone.utc)
        await torrent_stream.save()
        logger.info(f"Successfully updated {torrent_stream.id} metadata")
        return True

    except Exception as e:
        logger.error(
            f"Error updating metadata for {torrent_stream.id}: {str(e)}", exc_info=True
        )
        await _request_annotation(torrent_stream, processor.file_infos)
        return False


async def _request_annotation(
    torrent_stream: TorrentStreams, file_infos: list[FileInfo]
):
    """Request manual annotation for the torrent stream."""
    file_details = [KnownFile(filename=f.filename, size=f.size) for f in file_infos]
    torrent_stream.known_file_details = file_details
    torrent_stream.annotation_requested_at = datetime.now(tz=timezone.utc)
    await torrent_stream.save()

    # Notify contributors
    await telegram_notifier.send_file_annotation_request(torrent_stream.id)
    logger.info(f"Requested annotation for {torrent_stream.id}")
