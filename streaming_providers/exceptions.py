class ProviderException(Exception):
    def __init__(self, message, video_file_name):
        self.message = message
        self.video_file_name = video_file_name
        super().__init__(self.message)
