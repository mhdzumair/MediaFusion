from enum import StrEnum


# Enums
class MediaType(StrEnum):
    MOVIE = "movie"
    SERIES = "series"
    TV = "tv"
    EVENTS = "events"


class TorrentType(StrEnum):
    PUBLIC = "public"
    SEMI_PRIVATE = "semi-private"
    PRIVATE = "private"
    WEB_SEED = "web-seed"


class NudityStatus(StrEnum):
    NONE = "None"
    MILD = "Mild"
    MODERATE = "Moderate"
    SEVERE = "Severe"
    UNKNOWN = "Unknown"
    DISABLE = "Disable"
