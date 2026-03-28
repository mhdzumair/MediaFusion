"""Streaming provider errors surfaced as short static videos during playback."""

# Plays Usenet-specific on-screen text (not the torrent/magnet transfer_error clip).
USENET_TRANSFER_ERROR_VIDEO = "usenet_transfer_error.mp4"


class ProviderException(Exception):
    def __init__(self, message, video_file_name, retryable: bool = False):
        self.message = message
        self.video_file_name = video_file_name
        self.retryable = retryable
        super().__init__(self.message)
