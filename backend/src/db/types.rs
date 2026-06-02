/// Schema-derived type definitions for compile-time SQL safety.
///
/// # Enums
/// Every Postgres enum in the schema (`migrations/0001_baseline.up.sql`) is represented
/// as a native Rust enum deriving `sqlx::Type`. This means:
/// - Binding: `.bind(MediaType::Movie)` encodes correctly — no `::mediatype` cast needed
/// - Decoding: struct fields typed as `MediaType` decode directly — no `::text` cast needed
/// - Wrong variant / missing cast → **compile error**, not a silent runtime failure
///
/// # ID newtypes
/// Every internal primary-key / foreign-key column in the schema is `integer` (INT4).
/// Using `i64` for these fields is always wrong and causes sqlx decode errors at runtime.
/// The newtype wrappers below use `#[sqlx(transparent)]` so sqlx treats them as `i32` —
/// correct for all INT4 columns.
///
/// # Legitimate i64 columns (do NOT use i32/newtypes for these)
/// The only `bigint` (INT8) columns in the entire schema are byte-size and external-id fields:
/// - `stream_file.size`, `http_stream.size`, `torrent_stream.total_size`,
///   `usenet_stream.size`, `telegram_stream.size`
/// - `telegram_stream.document_id`, `telegram_user_forward.telegram_user_id`
/// - `stream_media_link.file_size`
// ─── Macro for ID newtypes ────────────────────────────────────────────────────
macro_rules! db_id {
    ($(#[$meta:meta])* $name:ident) => {
        $(#[$meta])*
        #[derive(
            Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash,
            serde::Serialize, serde::Deserialize,
            sqlx::Type,
        )]
        #[serde(transparent)]
        #[sqlx(transparent)]
        pub struct $name(pub i32);

        impl From<i32> for $name {
            fn from(v: i32) -> Self { Self(v) }
        }
        impl From<$name> for i32 {
            fn from(v: $name) -> Self { v.0 }
        }
        impl std::fmt::Display for $name {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                self.0.fmt(f)
            }
        }
    };
}

// ─── Wire-format helpers for enums ────────────────────────────────────────────

macro_rules! wire_enum_lower {
    ($ty:ident { $($variant:ident => $wire:literal),* $(,)? }) => {
        impl $ty {
            pub fn from_wire(s: &str) -> Option<Self> {
                match s.to_ascii_lowercase().as_str() {
                    $($wire => Some(Self::$variant),)*
                    _ => None,
                }
            }
            pub fn as_wire(&self) -> &'static str {
                match self {
                    $(Self::$variant => $wire,)*
                }
            }
        }
    };
}

macro_rules! wire_enum_screaming {
    ($ty:ident { $($variant:ident => $wire:literal),* $(,)? }) => {
        impl $ty {
            pub fn from_wire(s: &str) -> Option<Self> {
                match s.to_ascii_uppercase().as_str() {
                    $($wire => Some(Self::$variant),)*
                    _ => None,
                }
            }
            pub fn as_wire(&self) -> &'static str {
                match self {
                    $(Self::$variant => $wire,)*
                }
            }
        }
    };
}

// ─── Internal ID newtypes ─────────────────────────────────────────────────────

db_id!(
    /// Primary key / FK referencing `media.id` (INT4).
    MediaId
);
db_id!(
    /// Primary key / FK referencing `stream.id` (INT4).
    StreamId
);
db_id!(
    /// Primary key / FK referencing `users.id` (INT4).
    UserId
);
db_id!(
    /// Primary key / FK referencing `user_profiles.id` (INT4).
    ProfileId
);
db_id!(
    /// Primary key of `stream_file.id` (INT4). Not the bigint `size` column.
    StreamFileId
);
db_id!(
    /// Primary key / FK referencing `series_metadata.id` (INT4).
    SeriesId
);
db_id!(
    /// Primary key / FK referencing `season.id` (INT4).
    SeasonId
);
db_id!(
    /// Primary key / FK referencing `episode.id` (INT4).
    EpisodeId
);
db_id!(
    /// Primary key / FK referencing `genre.id` (INT4).
    GenreId
);
db_id!(
    /// Primary key / FK referencing `profile_integration.id` (INT4).
    IntegrationId
);

// ─── Postgres enum types ──────────────────────────────────────────────────────
//
// Source of truth: `backend/migrations/0001_baseline.up.sql`
// These must stay in sync with the `CREATE TYPE` declarations in that file.
//
// `#[serde(rename_all)]` matches the JSON/API wire form (may differ from DB SCREAMING_SNAKE).
// `#[sqlx(rename_all)]` matches Postgres enum labels.
// Use `from_wire` / `as_wire` for lenient string parsing outside serde.

/// `media.type` column.  Postgres type: `mediatype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "lowercase")]
#[sqlx(type_name = "mediatype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum MediaType {
    Movie,
    Series,
    Tv,
    Events,
}

wire_enum_lower!(MediaType {
    Movie => "movie",
    Series => "series",
    Tv => "tv",
    Events => "events",
});

/// `stream.stream_type` column.  Postgres type: `streamtype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "streamtype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum StreamType {
    Torrent,
    Http,
    Youtube,
    Usenet,
    Telegram,
    #[serde(rename = "EXTERNAL_LINK")]
    #[sqlx(rename = "EXTERNAL_LINK")]
    ExternalLink,
    Acestream,
}

wire_enum_screaming!(StreamType {
    Torrent => "TORRENT",
    Http => "HTTP",
    Youtube => "YOUTUBE",
    Usenet => "USENET",
    Telegram => "TELEGRAM",
    ExternalLink => "EXTERNAL_LINK",
    Acestream => "ACESTREAM",
});

/// `watch_history.action` column.  Postgres type: `watchaction`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "watchaction", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum WatchAction {
    Watched,
    Downloaded,
    Queued,
}

wire_enum_screaming!(WatchAction {
    Watched => "WATCHED",
    Downloaded => "DOWNLOADED",
    Queued => "QUEUED",
});

/// `watch_history.source` column.  Postgres type: `historysource`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "historysource", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum HistorySource {
    Mediafusion,
    Trakt,
    Simkl,
    Manual,
}

wire_enum_screaming!(HistorySource {
    Mediafusion => "MEDIAFUSION",
    Trakt => "TRAKT",
    Simkl => "SIMKL",
    Manual => "MANUAL",
});

/// `profile_integration.platform` column.  Postgres type: `integrationtype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "lowercase")]
#[sqlx(type_name = "integrationtype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum IntegrationType {
    Trakt,
    Simkl,
    Mal,
    Letterboxd,
    Anilist,
    Tvtime,
}

wire_enum_lower!(IntegrationType {
    Trakt => "trakt",
    Simkl => "simkl",
    Mal => "mal",
    Letterboxd => "letterboxd",
    Anilist => "anilist",
    Tvtime => "tvtime",
});

/// `file_media_link.link_source` column.  Postgres type: `linksource`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "linksource", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum LinkSource {
    User,
    #[serde(rename = "PTT_PARSER")]
    #[sqlx(rename = "PTT_PARSER")]
    PttParser,
    #[serde(rename = "TORRENT_METADATA")]
    #[sqlx(rename = "TORRENT_METADATA")]
    TorrentMetadata,
    #[serde(rename = "DEBRID_REALDEBRID")]
    #[sqlx(rename = "DEBRID_REALDEBRID")]
    DebridRealdebrid,
    #[serde(rename = "DEBRID_ALLDEBRID")]
    #[sqlx(rename = "DEBRID_ALLDEBRID")]
    DebridAlldebrid,
    #[serde(rename = "DEBRID_PREMIUMIZE")]
    #[sqlx(rename = "DEBRID_PREMIUMIZE")]
    DebridPremiumize,
    #[serde(rename = "DEBRID_TORBOX")]
    #[sqlx(rename = "DEBRID_TORBOX")]
    DebridTorbox,
    #[serde(rename = "DEBRID_DEBRIDLINK")]
    #[sqlx(rename = "DEBRID_DEBRIDLINK")]
    DebridDebridlink,
    Manual,
    Filename,
}

wire_enum_screaming!(LinkSource {
    User => "USER",
    PttParser => "PTT_PARSER",
    TorrentMetadata => "TORRENT_METADATA",
    DebridRealdebrid => "DEBRID_REALDEBRID",
    DebridAlldebrid => "DEBRID_ALLDEBRID",
    DebridPremiumize => "DEBRID_PREMIUMIZE",
    DebridTorbox => "DEBRID_TORBOX",
    DebridDebridlink => "DEBRID_DEBRIDLINK",
    Manual => "MANUAL",
    Filename => "FILENAME",
});

/// `stream_file.file_type` column.  Postgres type: `filetype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "filetype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum FileType {
    Video,
    Audio,
    Subtitle,
    Archive,
    Sample,
    Trailer,
    Nfo,
    Other,
}

wire_enum_screaming!(FileType {
    Video => "VIDEO",
    Audio => "AUDIO",
    Subtitle => "SUBTITLE",
    Archive => "ARCHIVE",
    Sample => "SAMPLE",
    Trailer => "TRAILER",
    Nfo => "NFO",
    Other => "OTHER",
});

/// `media.nudity_status` column.  Postgres type: `nuditystatus`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "nuditystatus", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum NudityStatus {
    None,
    Mild,
    Moderate,
    Severe,
    Unknown,
    Disable,
}

wire_enum_screaming!(NudityStatus {
    None => "NONE",
    Mild => "MILD",
    Moderate => "MODERATE",
    Severe => "SEVERE",
    Unknown => "UNKNOWN",
    Disable => "DISABLE",
});

/// Parse user profile nudity filter strings into Postgres enum values for SQL `ALL()` binds.
pub fn nudity_statuses_from_filter(excludes: &[String]) -> Vec<NudityStatus> {
    excludes
        .iter()
        .filter_map(|s| NudityStatus::from_wire(s))
        .collect()
}

/// `torrent_stream.torrent_type` column.  Postgres type: `torrenttype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "torrenttype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TorrentType {
    Public,
    SemiPrivate,
    Private,
    WebSeed,
}

wire_enum_screaming!(TorrentType {
    Public => "PUBLIC",
    SemiPrivate => "SEMI_PRIVATE",
    Private => "PRIVATE",
    WebSeed => "WEB_SEED",
});

/// `tracker.status` column.  Postgres type: `trackerstatus`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "trackerstatus", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TrackerStatus {
    Working,
    Failing,
    Unknown,
}

wire_enum_screaming!(TrackerStatus {
    Working => "WORKING",
    Failing => "FAILING",
    Unknown => "UNKNOWN",
});

/// `users.role` column.  Postgres type: `userrole`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "userrole", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum UserRole {
    User,
    PaidUser,
    Moderator,
    Admin,
}

wire_enum_screaming!(UserRole {
    User => "USER",
    PaidUser => "PAID_USER",
    Moderator => "MODERATOR",
    Admin => "ADMIN",
});

impl UserRole {
    /// Lowercase API / JWT wire form (`user`, `paid_user`, `moderator`, `admin`).
    pub fn as_api_wire(&self) -> &'static str {
        match self {
            Self::User => "user",
            Self::PaidUser => "paid_user",
            Self::Moderator => "moderator",
            Self::Admin => "admin",
        }
    }
}

/// `contributions.status` column.  Postgres type: `contributionstatus`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "contributionstatus", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ContributionStatus {
    Pending,
    Approved,
    Rejected,
}

wire_enum_screaming!(ContributionStatus {
    Pending => "PENDING",
    Approved => "APPROVED",
    Rejected => "REJECTED",
});

/// `iptv_source.source_type` column.  Postgres type: `iptvsourcetype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "iptvsourcetype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum IptvSourceType {
    M3u,
    Xtream,
    Stalker,
}

wire_enum_screaming!(IptvSourceType {
    M3u => "M3U",
    Xtream => "XTREAM",
    Stalker => "STALKER",
});

/// Declared in the schema but not yet used as a column type.
/// Postgres type: `downloadstatus`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize, sqlx::Type,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[sqlx(type_name = "downloadstatus", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DownloadStatus {
    Completed,
    Failed,
    Cancelled,
}

wire_enum_screaming!(DownloadStatus {
    Completed => "COMPLETED",
    Failed => "FAILED",
    Cancelled => "CANCELLED",
});

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, to_string};

    #[test]
    fn media_id_serializes_as_bare_integer() {
        assert_eq!(to_string(&MediaId(5)).unwrap(), "5");
        assert_eq!(
            to_string(&json!({"id": MediaId(42)})).unwrap(),
            r#"{"id":42}"#
        );
    }

    #[test]
    fn media_type_wire_round_trip() {
        assert_eq!(MediaType::Movie.as_wire(), "movie");
        assert_eq!(to_string(&MediaType::Movie).unwrap(), r#""movie""#);
        assert_eq!(MediaType::from_wire("Movie"), Some(MediaType::Movie));
        assert_eq!(MediaType::from_wire("SERIES"), Some(MediaType::Series));
    }

    #[test]
    fn integration_type_wire_round_trip() {
        assert_eq!(IntegrationType::Trakt.as_wire(), "trakt");
        assert_eq!(to_string(&IntegrationType::Trakt).unwrap(), r#""trakt""#);
        assert_eq!(
            IntegrationType::from_wire("TRAKT"),
            Some(IntegrationType::Trakt)
        );
    }

    #[test]
    fn watch_action_wire_round_trip() {
        assert_eq!(WatchAction::Watched.as_wire(), "WATCHED");
        assert_eq!(to_string(&WatchAction::Watched).unwrap(), r#""WATCHED""#);
        assert_eq!(
            WatchAction::from_wire("watched"),
            Some(WatchAction::Watched)
        );
    }

    #[test]
    fn stream_type_wire_round_trip() {
        assert_eq!(StreamType::Torrent.as_wire(), "TORRENT");
        assert_eq!(to_string(&StreamType::Torrent).unwrap(), r#""TORRENT""#);
        assert_eq!(StreamType::from_wire("torrent"), Some(StreamType::Torrent));
        assert_eq!(
            StreamType::from_wire("external_link"),
            Some(StreamType::ExternalLink)
        );
    }

    #[test]
    fn user_id_deserializes_from_integer() {
        let id: MediaId = serde_json::from_str("123").unwrap();
        assert_eq!(id, MediaId(123));
    }
}
