from typing import Any, Optional

import PTT

from streaming_providers.exceptions import ProviderException
from utils.validation_helper import is_video_file


def select_file_index_from_torrent(
    torrent_info: dict[str, Any],
    filename: Optional[str],
    file_index: Optional[int],
    episode: Optional[int] = None,
    file_key: str = "files",
    name_key: str = "name",
    size_key: str = "size",
    selected_key: Optional[str] = None,
    add_leading_slash: bool = False,
    file_size_callback: Optional[callable] = None,
) -> int:
    """Select the file index from the torrent info."""
    files = torrent_info[file_key]
    if selected_key:
        files = [file for file in files if file[selected_key] == 1]

    if file_index is not None and file_index < len(files):
        if is_video_file(files[file_index][name_key]):
            return file_index

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
        files = file_size_callback(files)

    # If no file index is provided, select the largest file
    largest_file = max(files, key=lambda file: file[size_key])
    index = files.index(largest_file)
    if is_video_file(largest_file[name_key]):
        return index

    raise ProviderException(
        "No matching file available for this torrent", "no_matching_file.mp4"
    )
