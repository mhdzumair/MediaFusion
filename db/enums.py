from enum import StrEnum


# Enums
class MediaType(StrEnum):
    MOVIE = "movie"
    SERIES = "series"
    TV = "tv"
    EVENTS = "events"


class IndexerType(StrEnum):
    FREELEACH = "freeleech"
    SEMI_PRIVATE = "semi-private"
    PRIVATE = "private"


class NudityStatus(StrEnum):
    NONE = "None"
    MILD = "Mild"
    MODERATE = "Moderate"
    SEVERE = "Severe"
    UNKNOWN = "Unknown"
