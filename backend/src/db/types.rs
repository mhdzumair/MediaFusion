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

/// `media.type` column.  Postgres type: `mediatype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "mediatype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum MediaType {
    Movie,
    Series,
    Tv,
    Events,
}

/// `stream.stream_type` column.  Postgres type: `streamtype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "streamtype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum StreamType {
    Torrent,
    Http,
    Youtube,
    Usenet,
    Telegram,
    #[sqlx(rename = "EXTERNAL_LINK")]
    ExternalLink,
    Acestream,
}

/// `watch_history.action` column.  Postgres type: `watchaction`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "watchaction", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum WatchAction {
    Watched,
    Downloaded,
    Queued,
}

/// `watch_history.source` column.  Postgres type: `historysource`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "historysource", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum HistorySource {
    Mediafusion,
    Trakt,
    Simkl,
    Manual,
}

/// `profile_integration.platform` column.  Postgres type: `integrationtype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "integrationtype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum IntegrationType {
    Trakt,
    Simkl,
    Mal,
    Letterboxd,
    Anilist,
    Tvtime,
}

/// `file_media_link.link_source` column.  Postgres type: `linksource`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "linksource", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum LinkSource {
    User,
    #[sqlx(rename = "PTT_PARSER")]
    PttParser,
    #[sqlx(rename = "TORRENT_METADATA")]
    TorrentMetadata,
    #[sqlx(rename = "DEBRID_REALDEBRID")]
    DebridRealdebrid,
    #[sqlx(rename = "DEBRID_ALLDEBRID")]
    DebridAlldebrid,
    #[sqlx(rename = "DEBRID_PREMIUMIZE")]
    DebridPremiumize,
    #[sqlx(rename = "DEBRID_TORBOX")]
    DebridTorbox,
    #[sqlx(rename = "DEBRID_DEBRIDLINK")]
    DebridDebridlink,
    Manual,
    Filename,
}

/// `stream_file.file_type` column.  Postgres type: `filetype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
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

/// `media.nudity_status` column.  Postgres type: `nuditystatus`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "nuditystatus", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum NudityStatus {
    None,
    Mild,
    Moderate,
    Severe,
    Unknown,
    Disable,
}

/// `torrent_stream.torrent_type` column.  Postgres type: `torrenttype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "torrenttype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TorrentType {
    Public,
    SemiPrivate,
    Private,
    WebSeed,
}

/// `tracker.status` column.  Postgres type: `trackerstatus`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "trackerstatus", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TrackerStatus {
    Working,
    Failing,
    Unknown,
}

/// `users.role` column.  Postgres type: `userrole`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "userrole", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum UserRole {
    User,
    PaidUser,
    Moderator,
    Admin,
}

/// `contributions.status` column.  Postgres type: `contributionstatus`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "contributionstatus", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ContributionStatus {
    Pending,
    Approved,
    Rejected,
}

/// `iptv_source.source_type` column.  Postgres type: `iptvsourcetype`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "iptvsourcetype", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum IptvSourceType {
    M3u,
    Xtream,
    Stalker,
}

/// Declared in the schema but not yet used as a column type.
/// Postgres type: `downloadstatus`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash,
    serde::Serialize, serde::Deserialize,
    sqlx::Type,
)]
#[sqlx(type_name = "downloadstatus", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DownloadStatus {
    Completed,
    Failed,
    Cancelled,
}
