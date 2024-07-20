from typing import Any, Optional

import PTT


def select_file_index_from_torrent(
    torrent_info: dict[str, Any],
    filename: str,
    file_index: int,
    episode: Optional[int] = None,
    file_key: str = "files",
    name_key: str = "name",
    size_key: str = "size",
    selected_key: Optional[str] = None,
    add_leading_slash: bool = False,
) -> int:
    """Select the file index from the torrent info."""
    files = torrent_info[file_key]
    if selected_key:
        files = [file for file in files if file[selected_key] == 1]

    if file_index is not None and file_index < len(files):
        return file_index

    if filename:
        if add_leading_slash:
            filename = "/" + filename
        for index, file in enumerate(files):
            if file[name_key] == filename:
                return index

    if episode:
        # Select the file with the matching episode number
        for index, file in enumerate(files):
            if episode in PTT.parse_title(file[name_key]).get("episodes", []):
                return index

    # If no file index is provided, select the largest file
    largest_file = max(files, key=lambda file: file[size_key])
    return files.index(largest_file)
