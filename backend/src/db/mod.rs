pub mod types;
pub use types::{
    // Postgres enum types (bind/decode directly — no ::enumname or ::text casts needed)
    ContributionStatus,
    DownloadStatus,
    // ID newtypes (all INT4 — use i32 via these, never i64 for internal IDs)
    EpisodeId,
    FileType,
    GenreId,
    HistorySource,
    IntegrationId,
    IntegrationType,
    IptvSourceType,
    LinkSource,
    MediaId,
    MediaType,
    NudityStatus,
    ProfileId,
    SeasonId,
    SeriesId,
    StreamFileId,
    StreamId,
    StreamType,
    TorrentType,
    TrackerStatus,
    UserId,
    UserRole,
    WatchAction,
};

pub mod catalog;
pub mod genres;
pub mod media;
pub mod meta;
pub mod pool;
pub mod streams;
pub mod telegram;
pub mod telegram_channels;
pub mod torznab;
pub mod watch_history;

pub use media::{
    get_media_id_by_external_id, get_media_meta, load_aka_titles, resolve_media_ids,
    search_media_candidates,
};
pub use streams::{
    fetch_acestream_streams_bulk, fetch_http_streams_bulk, fetch_stream_playback_info,
    fetch_streams_bulk, fetch_telegram_streams_bulk, fetch_tv_streams_for_media,
    fetch_usenet_streams_bulk, fetch_youtube_streams_bulk, filter_existing_hashes,
    link_torrent_trackers, upsert_stream_files, usenet_row_to_stremio, StreamPlaybackInfo,
    TorrentFileEntry,
};
