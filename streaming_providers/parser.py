import logging
from datetime import datetime, timezone
from typing import Any, Optional

import PTT

from db.models import TorrentStreams, Episode, Season
from streaming_providers.exceptions import ProviderException
from utils.validation_helper import is_video_file


async def select_file_index_from_torrent(
    torrent_info: dict[str, Any],
    filename: Optional[str],
    episode: Optional[int] = None,
    file_key: str = "files",
    name_key: str = "name",
    size_key: str = "size",
    add_leading_slash: bool = False,
    file_size_callback: Optional[callable] = None,
) -> int:
    """Select the file index from the torrent info."""
    files = torrent_info[file_key]
    if filename:
        if add_leading_slash:
            filename = "/" + filename
        for index, file in enumerate(files):
            if file[name_key] == filename and is_video_file(file[name_key]):
                return index

    if episode:
        # Select the file with the matching episode number
        for index, file in enumerate(files):
            if episode in PTT.parse_title(file[name_key]).get(
                "episodes", []
            ) and is_video_file(file[name_key]):
                return index
        raise ProviderException(
            "No matching file available for this torrent", "no_matching_file.mp4"
        )

    if file_size_callback:
        # Get the file sizes
        await file_size_callback(files)

    # If no file index is provided, select the largest file
    largest_file = max(files, key=lambda file: file[size_key])
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
    remove_leading_slash: bool = False,
    is_index_trustable: bool = False,
):
    files_data = torrent_info[file_key]
    if season is None:
        torrent_stream.filename = (
            files_data[file_index][name_key].lstrip("/")
            if remove_leading_slash
            else files_data[file_index][name_key]
        )
        if file_index is not None and is_index_trustable:
            torrent_stream.file_index = file_index
        torrent_stream.updated_at = datetime.now(timezone.utc)
        await torrent_stream.save()
        logging.info(f"Updated {torrent_stream.id} metadata")

    else:
        parsed_data = [
            {
                "filename": (
                    file[name_key].lstrip("/")
                    if remove_leading_slash
                    else file[name_key]
                ),
                "size": file.get(size_key),
                "index": idx,
                **PTT.parse_title(file[name_key]),
            }
            for idx, file in enumerate(files_data)
        ]
        episodes = [
            Episode(
                episode_number=file["episodes"][0],
                filename=file["filename"],
                size=file["size"],
                file_index=file["index"] if is_index_trustable else None,
            )
            for file in parsed_data
            if season in file["seasons"] and file["episodes"]
        ]
        if not episodes:
            return

        torrent_stream.season = Season(
            season_number=season,
            episodes=episodes,
        )
        total_size = sum(file["size"] for file in parsed_data if file["size"])
        if total_size > torrent_stream.size:
            torrent_stream.size = total_size
        torrent_stream.updated_at = datetime.now()
        await torrent_stream.save()
        logging.info(f"Updated {torrent_stream.id} episode data")
