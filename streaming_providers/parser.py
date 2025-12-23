import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from os.path import basename
from typing import Any, Optional, TypedDict, Tuple, Pattern, Callable

import PTT
import dateparser

from db.schemas import TorrentStreamData, EpisodeFileData, KnownFile
from db import sql_crud
from db.database import get_read_session, get_async_session
from streaming_providers.exceptions import ProviderException
from utils.lock import acquire_redis_lock
from utils.runtime_const import DATE_STR_REGEX
from utils.telegram_bot import telegram_notifier
from utils.validation_helper import is_video_file

logger = logging.getLogger(__name__)

# Precompile all regex patterns for better performance
SEASON_EPISODE_PATTERNS: dict[
    str, Tuple[Pattern, Callable[[re.Match, int], Tuple[int, int]]]
] = {
    # Pattern 1: SxxExx format (most common and reliable)
    # Examples: "S01E04", "s01e04", "S1E04", "s1e04"
    "standard": (
        re.compile(r"[sS](\d{1,2})[eE](\d{1,2})"),
        lambda m, _: (int(m.group(1)), int(m.group(2))),
    ),
    # Pattern 2: xxXxx format
    # Examples: "1x01", "01x01", "1X01", "01X01"
    "separator_x": (
        re.compile(r"(?<!\w)(\d{1,2})[xX](\d{1,2})(?!\w)"),
        lambda m, _: (int(m.group(1)), int(m.group(2))),
    ),
    # Pattern 3: Season X Episode Y format (text-based)
    # Example: "Season 1 Episode 04", "season 01 episode 4"
    "text_based": (
        re.compile(r"[sS]eason\s+(\d{1,2}).*?[eE]pisode\s+(\d{1,2})"),
        lambda m, _: (int(m.group(1)), int(m.group(2))),
    ),
    # Pattern 4: Season/Series followed by episode number
    # Example: "Season 02 - 05", "Series.1.Ep.03"
    "season_ep": (
        re.compile(
            r"(?:[sS]eason|[sS]eries)[.\s-]*(\d{1,2}).*?(?:[eE]p|[eE]pisode)?[.\s-]*(\d{1,2})"
        ),
        lambda m, _: (int(m.group(1)), int(m.group(2))),
    ),
    # Pattern 5: SXXEXX format (no separator)
    # Examples: "S01E04", "S1E4"
    "no_separator": (
        re.compile(r"S(\d{1,2})E(\d{1,2})", re.IGNORECASE),
        lambda m, _: (int(m.group(1)), int(m.group(2))),
    ),
    # Pattern 6: Show name followed by episode number
    # Examples: "Show 01", "Show - 01", "Show.01"
    # Only apply this when it's likely an actual episode number, not part of a hash
    "simple_episode": (
        re.compile(r"(?<=\s)(\d{1,2})(?:\s|$|\.)"),
        lambda m, s: (s, int(m.group(1))),  # Default to season s
    ),
    # Pattern 7: bracketed season and episode
    # Examples: "[S01E04]", "(S1.E4)", "[1x04]"
    "bracketed": (
        re.compile(r"[\[\(](?:[sS])?(\d{1,2})[.\s]?(?:[eExX])(\d{1,2})[\]\)]"),
        lambda m, _: (int(m.group(1)), int(m.group(2))),
    ),
    # Pattern 8: season and episode separated by period
    # Examples: "1.04", "01.04"
    "period_sep": (
        re.compile(r"(?<!\d|\w)(\d{1,2})\.(\d{2})(?!\d|\w)"),
        lambda m, _: (int(m.group(1)), int(m.group(2))),
    ),
    # Pattern 9: episode identifier with underscore or dash
    # Examples: "_e01", "-e01", "_ep01", "-ep01"
    "episode_only": (
        re.compile(r"[_-][eE](?:p)?(\d{1,2})"),
        lambda m, s: (s, int(m.group(1))),  # Default to season s
    ),
    # Pattern 10: Absolute episode numbering
    # Examples: "Episode.123", "Ep 123", "EP.123"
    "absolute_ep": (
        re.compile(r"[eE]p(?:isode)?[.\s](\d{1,3})(?!\w)", re.IGNORECASE),
        lambda m, s: (s, int(m.group(1))),  # Default to season s
    ),
    # Pattern 11: Zero-padded episode numbers
    # Examples: "00022"
    "zero_padded": (
        re.compile(r"(?<!\d)(\d{2,})(?!\d)"),
        lambda m, s: (s, int(m.group(1))),  # Default to season s
    ),
}
# Additional patterns specifically for anime that should be tried after the general patterns
ANIME_PATTERNS: dict[
    str, Tuple[Pattern, Callable[[re.Match, int], Tuple[int, int]]]
] = {
    # Common pattern for standalone anime episodes, avoid matching hashes
    # This should identify patterns like " 01 ", " - 01", etc.
    "standalone_episode": (
        re.compile(r"(?:^|\s|\[|\()(\d{1,2})(?:\s|$|\]|\))"),
        lambda m, s: (s, int(m.group(1))),  # Default to season s
    ),
}


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
        self._episodes: Optional[list[EpisodeFileData]] = None
        self._metadata = None  # SeriesMetadata

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

    async def _parse_season_episode_info(
        self,
        file_info: FileInfo,
        torrent_title: str,
        meta_id: str,
        default_season: Optional[int] = 1,
    ) -> tuple[Optional[int], Optional[int]]:
        """Parse season and episode information from filename and torrent title."""
        # First try from filename with PTT
        parsed_data = PTT.parse_title(file_info.filename)
        seasons = parsed_data.get("seasons", [])
        episodes = parsed_data.get("episodes", [])

        if seasons and episodes:
            return seasons[0], episodes[0]

        # For season packs without explicit season number
        if not seasons and episodes:
            return default_season, episodes[0]

        # If no season/episode found, try from torrent title
        title_parsed = PTT.parse_title(torrent_title)
        title_seasons = title_parsed.get("seasons", [])
        title_episodes = title_parsed.get("episodes", [])

        # If only one season in title, use it
        if len(title_seasons) == 1 and len(title_episodes) == 1:
            return title_seasons[0], title_episodes[0]

        # if we have date in the title, use it to find the episode from metadata
        if not parsed_data.get("date"):
            date_str_match = DATE_STR_REGEX.search(file_info.filename)
            if date_str_match:
                parsed_data["date"] = dateparser.parse(
                    date_str_match.group(0)
                ).strftime("%Y-%m-%d")

        if parsed_data.get("date"):
            if not self._metadata:
                async for session in get_read_session():
                    self._metadata = await sql_crud.get_series_data_by_id(session, meta_id, load_relations=True)
            
            if self._metadata and self._metadata.seasons:
                # Search through all seasons and their episodes
                for season in self._metadata.seasons:
                    for episode in season.episodes:
                        if episode.released and episode.released.strftime("%Y-%m-%d") == parsed_data["date"]:
                            return season.season_number, episode.episode_number

            # if we have date in the title but no metadata, return None to avoid false detection
            return None, None

        # If PTT failed to identify season or episode, try the fallback parser
        season, episode = fallback_parse_season_episode(
            file_info.filename, default_season
        )

        # Log when fallback parser is used
        if season is not None or episode is not None:
            logger.info(
                f"Used fallback parser for file '{file_info.filename}'. "
                f"Detected: S{season}E{episode}"
            )

        return season, episode

    async def find_specific_episode(
        self, season: int, episode: int, torrent_title: str, meta_id: str
    ) -> Optional[FileInfo]:
        """Find a specific episode in the video files."""
        if self._episodes:
            for episode_file in self._episodes:
                if (
                    episode_file.season_number == season
                    and episode_file.episode_number == episode
                    and 0 <= episode_file.file_index < len(self.file_infos)
                ):
                    return self.file_infos[episode_file.file_index]

        video_files = self.get_video_files()
        for file_info in video_files:
            parsed_season, parsed_episode = await self._parse_season_episode_info(
                file_info, torrent_title, meta_id, default_season=season
            )
            if parsed_season == season and parsed_episode == episode:
                return file_info
        if len(video_files):
            # in some cases only one video file is present and no season/episode info
            return video_files[0]
        return None

    async def parse_all_episodes(
        self, torrent_title: str, meta_id: str, default_season: Optional[int] = None
    ) -> list[EpisodeFileData]:
        """Parse all episode information from video files."""
        episodes = []
        for file_info in self.get_video_files():
            season, episode = await self._parse_season_episode_info(
                file_info, torrent_title, meta_id, default_season
            )
            if season and episode:
                episodes.append(
                    EpisodeFileData(
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
    torrent_stream: TorrentStreamData,
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
            await _save_torrent_stream(torrent_stream)
            raise ProviderException(
                "No valid video files found in torrent. Torrent has been blocked.",
                "no_matching_file.mp4",
            )
        else:
            raise ProviderException(
                "No valid video files found in torrent",
                "no_matching_file.mp4",
            )

    # Update metadata if filename is trustable
    if is_filename_trustable:
        await update_torrent_streams_metadata(
            torrent_stream=torrent_stream,
            processor=processor,
            season=season,
            is_index_trustable=is_index_trustable,
        )

    # Try to find by season/episode
    if season and episode:
        selected_file = await processor.find_specific_episode(
            season, episode, torrent_stream.torrent_name, torrent_stream.meta_id
        )
        if selected_file:
            return selected_file.index
        else:
            # Found video files but couldn't match season/episode
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
        "no_matching_file.mp4",
    )


async def _save_torrent_stream(torrent_stream: TorrentStreamData) -> None:
    """Save torrent stream updates to PostgreSQL database."""
    updates = {
        "is_blocked": torrent_stream.is_blocked,
        "filename": torrent_stream.filename,
        "file_index": torrent_stream.file_index,
        "size": torrent_stream.size,
        "updated_at": datetime.now(tz=timezone.utc),
    }
    
    # Include known_file_details if present (serialize KnownFile objects to dicts)
    if torrent_stream.known_file_details:
        updates["known_file_details"] = [
            kf.model_dump() if hasattr(kf, 'model_dump') else {"filename": kf.filename, "size": kf.size}
            for kf in torrent_stream.known_file_details
        ]
    
    # Filter out None values except for is_blocked
    updates = {k: v for k, v in updates.items() if v is not None or k == "is_blocked"}
    
    async for session in get_async_session():
        await sql_crud.update_torrent_stream(session, torrent_stream.id, updates)
        
        # Handle episode files separately if needed
        if torrent_stream.episode_files:
            await sql_crud.update_episode_files(
                session, torrent_stream.id, torrent_stream.episode_files
            )


async def update_torrent_streams_metadata(
    torrent_stream: TorrentStreamData,
    processor: TorrentFileProcessor,
    season: Optional[int] = None,
    is_index_trustable: bool = False,
) -> bool:
    """
    Update torrent stream metadata using the provided processor instance.
    Returns True if update was successful, False if annotation was requested.
    """
    # Check if annotation was recently requested with redis lock for 3 days
    acquired, _ = await acquire_redis_lock(
        f"annotation_lock_{torrent_stream.id}", timeout=259200, block=False
    )
    if not acquired:
        logger.info(
            f"Skipping metadata update for {torrent_stream.id} - recent annotation request"
        )
        return False

    try:
        if season is None:
            # Movie handling
            file_info = processor.get_largest_video_file()
            if not file_info:
                torrent_stream.is_blocked = True
                await _save_torrent_stream(torrent_stream)
                logger.error(f"No video files found in torrent {torrent_stream.id}")
                return False

            torrent_stream.filename = file_info.filename
            if is_index_trustable:
                torrent_stream.file_index = file_info.index
            torrent_stream.size = file_info.size

        else:
            # Series handling
            video_files = processor.get_video_files()
            if not video_files:
                torrent_stream.is_blocked = True
                await _save_torrent_stream(torrent_stream)
                logger.error(f"No video files found in torrent {torrent_stream.id}")
                return False

            episodes = await processor.parse_all_episodes(
                torrent_stream.torrent_name,
                torrent_stream.meta_id,
                default_season=season,
            )

            if not episodes:
                if len(video_files) == 1 and len(torrent_stream.episode_files) == 1:
                    episodes = torrent_stream.episode_files
                    episodes[0].filename = video_files[0].filename
                    episodes[0].size = video_files[0].size
                    episodes[0].file_index = (
                        video_files[0].index if is_index_trustable else None
                    )
                else:
                    await _request_annotation(torrent_stream, processor.file_infos)
                    return False

            torrent_stream.episode_files = episodes
            total_size = sum(f.size for f in processor.get_video_files())
            if total_size > torrent_stream.size:
                torrent_stream.size = total_size

        torrent_stream.updated_at = datetime.now(tz=timezone.utc)
        await _save_torrent_stream(torrent_stream)
        logger.info(f"Successfully updated {torrent_stream.id} metadata")
        return True

    except Exception as e:
        logger.error(
            f"Error updating metadata for {torrent_stream.id}: {str(e)}", exc_info=True
        )
        return False


async def _request_annotation(
    torrent_stream: TorrentStreamData, file_infos: list[FileInfo]
):
    """Request manual annotation for the torrent stream."""
    file_details = [KnownFile(filename=f.filename, size=f.size) for f in file_infos]
    torrent_stream.known_file_details = file_details
    await _save_torrent_stream(torrent_stream)

    # Notify contributors
    await telegram_notifier.send_file_annotation_request(
        torrent_stream.id, torrent_stream.torrent_name
    )
    logger.info(f"Requested annotation for {torrent_stream.id}")


def is_likely_hash(match_str: str, filename: str) -> bool:
    """
    Check if a matched string is likely part of a hash by looking at surrounding context.
    Returns True if it's likely a hash, False otherwise.
    """

    # If the match is surrounded by hash indicators or hex characters, it's likely a hash
    match_pos = filename.find(match_str)
    if match_pos == -1:
        return False

    # Check if it's inside brackets which often contain hashes
    bracket_match = re.search(
        r"\[[^\]]*" + re.escape(match_str) + r"[^\[]*\]", filename
    )
    if bracket_match:
        # If the bracketed content is short, it might be an episode indicator
        # If it's long, it's more likely a hash
        content = bracket_match.group(0)
        if len(content) > 10 and any(c.isalpha() for c in content):
            return True

    # If the match contains letters (like in hex), it's likely a hash
    if any(c.isalpha() for c in match_str):
        return True

    return False


def fallback_parse_season_episode(
    filename: str, default_season: int = 1
) -> tuple[Optional[int], Optional[int]]:
    """
    Fallback parser for season and episode information.
    Returns (season_number, episode_number) tuple where each can be None if not detected.

    Uses precompiled patterns stored in a global dictionary for better performance.
    Patterns are ordered from most reliable/common to least reliable/specific.
    """
    # Extract base filename without path and remove common extensions
    base_filename = re.sub(r"\.(mkv|mp4|avi|mov|wmv|flv)$", "", filename.split("/")[-1])

    # First try specific, high-confidence patterns
    for pattern_name, (regex, extractor) in SEASON_EPISODE_PATTERNS.items():
        match = regex.search(base_filename)
        if match:
            # Ensure the match isn't likely part of a hash
            if pattern_name in ["simple_episode"] and is_likely_hash(
                match.group(0), base_filename
            ):
                continue

            season, episode = extractor(match, default_season)
            return season, episode

    # Additional patterns specifically for anime titles should be matched only as a last resort
    # and avoided if the string looks like it contains a hash
    # First check if there's a standalone number that's likely an episode
    # But be very careful with anime files to avoid matching hash portions
    for pattern_name, (regex, extractor) in ANIME_PATTERNS.items():
        for match in regex.finditer(base_filename):
            # Skip if the match is inside a hash-like structure
            if is_likely_hash(match.group(0), base_filename):
                continue

            # Find position relative to the title
            # Look for numbers that appear after the anime title but before any hash
            match_pos = match.start()

            # Very simple heuristic: if it's early in the filename or has space around it
            # and is a small number (less than 100), it's likely an episode number
            if match_pos > 5 and int(match.group(1)) < 100:
                season, episode = extractor(match, default_season)
                return season, episode

    # No match found
    return None, None
