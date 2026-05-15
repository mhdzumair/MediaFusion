from enum import StrEnum


# User role enum
class UserRole(StrEnum):
    USER = "user"
    PAID_USER = "paid_user"
    MODERATOR = "moderator"
    ADMIN = "admin"


# Contribution status enum
class ContributionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# Download status enum (deprecated - kept for migration)
class DownloadStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Watch action enum - tracks what the user did with content
class WatchAction(StrEnum):
    WATCHED = "watched"
    DOWNLOADED = "downloaded"
    QUEUED = "queued"


# History source enum - tracks where the history entry came from
class HistorySource(StrEnum):
    MEDIAFUSION = "mediafusion"  # Native MediaFusion tracking
    TRAKT = "trakt"  # Imported from Trakt
    SIMKL = "simkl"  # Imported from Simkl
    MANUAL = "manual"  # Manually added by user


# External platform integration types
class IntegrationType(StrEnum):
    TRAKT = "trakt"
    SIMKL = "simkl"
    MAL = "myanimelist"
    LETTERBOXD = "letterboxd"
    ANILIST = "anilist"
    TVTIME = "tvtime"


# Sync direction for integrations
class SyncDirection(StrEnum):
    EXPORT = "export"
    IMPORT = "import"
    BIDIRECTIONAL = "bidirectional"


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


# Vote types for streams
class VoteType(StrEnum):
    UP = "up"
    DOWN = "down"


# Stream quality status
class QualityStatus(StrEnum):
    WORKING = "working"
    BROKEN = "broken"
    GOOD_QUALITY = "good_quality"
    POOR_QUALITY = "poor_quality"


# Metadata vote types - simplified to likes for popularity
class MetadataVoteType(StrEnum):
    LIKE = "like"


# Suggestion status
class SuggestionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# Stream suggestion types
class StreamSuggestionType(StrEnum):
    REPORT_BROKEN = "report_broken"  # Report that stream doesn't work
    QUALITY_CORRECTION = "quality_correction"  # Fix resolution, codec, etc.
    LANGUAGE_CORRECTION = "language_correction"  # Fix language/audio info
    OTHER = "other"  # Other issues


# IPTV source types for import
class IPTVSourceType(StrEnum):
    M3U = "m3u"
    XTREAM = "xtream"
    STALKER = "stalker"  # Future support
