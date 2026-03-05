class ProviderException(Exception):
    def __init__(self, message, video_file_name, retryable: bool = False):
        self.message = message
        self.video_file_name = video_file_name
        self.retryable = retryable
        super().__init__(self.message)
