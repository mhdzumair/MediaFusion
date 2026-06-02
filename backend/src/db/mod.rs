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
pub mod metadata_merge;
pub mod metadata_model;
pub mod metadata_store;
pub mod pool;
pub mod stream_links;
pub mod stream_model;
pub mod stream_store;
pub mod streams;
pub mod telegram;
pub mod telegram_channels;
pub mod torznab;
pub mod watch_history;

pub use media::{
    get_media_id_by_external_id, get_media_meta, load_aka_titles, resolve_media_ids,
    search_media_candidates,
};
pub use metadata_merge::merge_normalized;
pub use metadata_model::{
    NormalizedAkaTitle, NormalizedCastMember, NormalizedCrewMember, NormalizedEpisode,
    NormalizedMetadata, NormalizedRating, NormalizedSeason, NormalizedTrailer, StoreMediaOpts,
};
pub use metadata_store::{
    find_existing_media, link_genre, link_to_catalogs, store_external_id, store_media,
    upsert_primary_image,
};
pub use stream_links::{
    link_stream_audio_channels, link_stream_audio_formats, link_stream_hdr_formats,
    link_stream_languages, link_stream_to_media, link_stream_to_media_with_flags,
    link_torrent_trackers_for_stream,
};
pub use stream_model::{
    resolve_series_episode_numbers, AcestreamStoreInput, HttpStoreInput, StoreStreamOpts,
    StreamFileStoreInput, StreamStoreBase, TelegramStoreInput, TorrentStoreInput, UsenetStoreInput,
    YoutubeStoreInput,
};
pub use stream_store::{
    link_file_to_media_episode, store_acestream_stream, store_http_stream, store_telegram_stream,
    store_telegram_streams, store_torrent_stream, store_torrent_streams, store_usenet_stream,
    store_usenet_streams, store_youtube_stream, strip_nul, upsert_stream_file_row,
    upsert_torrent_files_by_hash, StoreStreamResult,
};
pub use streams::{
    fetch_acestream_streams_bulk, fetch_http_streams_bulk, fetch_stream_playback_info,
    fetch_streams_bulk, fetch_telegram_streams_bulk, fetch_tv_streams_for_media,
    fetch_usenet_streams_bulk, fetch_youtube_streams_bulk, filter_existing_hashes,
    link_torrent_trackers, upsert_stream_files, usenet_row_to_stremio, StreamPlaybackInfo,
    TorrentFileEntry,
};
