import logging
from datetime import datetime, timezone
from typing import Any, Optional
from os.path import basename

import PTT

from db.models import TorrentStreams, EpisodeFile
from streaming_providers.exceptions import ProviderException
from utils.validation_helper import is_video_file


async def select_file_index_from_torrent(
    torrent_info: dict[str, Any],
    filename: Optional[str],
    season: Optional[int],
    episode: Optional[int] = None,
    file_key: str = "files",
    name_key: str = "name",
    size_key: str = "size",
    file_size_callback: Optional[callable] = None,
) -> int:
    """Select the file index from the torrent info."""
    files = torrent_info[file_key]
    if filename:
        # Select the file with the matching filename
        for index, file in enumerate(files):
            if basename(file[name_key]) == filename and is_video_file(filename):
                return index

    if season and episode:
        # Select the file with the matching episode number
        for index, file in enumerate(files):
            if not is_video_file(file[name_key]):
                continue
            season_parsed_data = PTT.parse_title(file[name_key])
            found_season = season_parsed_data.get("seasons")
            if (
                found_season and season in found_season
            ) or episode in season_parsed_data.get("episodes"):
                return index
        raise ProviderException(
            "No matching file available for this torrent", "no_matching_file.mp4"
        )

    if file_size_callback:
        # Get the file sizes
        await file_size_callback(files)

    # If no file index is provided, select the largest file
    largest_file = max(files, key=lambda file_: file_[size_key])
    index = files.index(largest_file)
    if is_video_file(largest_file[name_key]):
        return index

    raise ProviderException(
        "No matching file available for this torrent", "no_matching_file.mp4"
    )


async def update_torrent_streams_metadata(
    torrent_stream: TorrentStreams,
    torrent_info: dict,
    file_index: int,
    season: Optional[int] = None,
    file_key: str = "files",
    name_key: str = "name",
    size_key: str = "size",
    is_index_trustable: bool = False,
):
    files_data = torrent_info[file_key]
    if season is None:
        torrent_stream.filename = basename(files_data[file_index][name_key])
        if file_index is not None and is_index_trustable:
            torrent_stream.file_index = file_index
        torrent_stream.updated_at = datetime.now(timezone.utc)
        await torrent_stream.save()
        logging.info(f"Updated {torrent_stream.id} metadata")

    else:
        title_parsed_data = PTT.parse_title(torrent_stream.torrent_name)
        episodes = []
        for idx, file in enumerate(files_data):
            file_parsed_data = PTT.parse_title(file[name_key])
            season_number = file_parsed_data.get("seasons") or None
            if (
                season_number is None
                and title_parsed_data.get("seasons")
                and len(title_parsed_data["seasons"]) == 1
            ):
                season_number = title_parsed_data["seasons"][0]
            episode_number = file_parsed_data.get("episodes") or None
            if (
                episode_number is None
                and title_parsed_data.get("episodes")
                and len(title_parsed_data["episodes"]) == 1
            ):
                episode_number = title_parsed_data["episodes"][0]

            if not season_number or not episode_number:
                continue

            episodes.append(
                EpisodeFile(
                    season_number=season_number,
                    episode_number=episode_number,
                    filename=basename(file[name_key]),
                    size=file[size_key],
                    file_index=idx if is_index_trustable else None,
                )
            )

        if not episodes:
            return

        torrent_stream.episode_files = episodes
        total_size = sum(file[size_key] for file in files_data)
        if total_size > torrent_stream.size:
            torrent_stream.size = total_size
        torrent_stream.updated_at = datetime.now()
        await torrent_stream.save()
        logging.info(f"Updated {torrent_stream.id} episode data")
