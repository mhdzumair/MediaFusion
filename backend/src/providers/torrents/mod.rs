/// Torrent / debrid streaming provider implementations.
///
/// Each sub-module owns one provider's `get_video_url`, `check_cached`, and
/// (where supported) `delete_all_torrents` logic.
///
/// `cache` owns the Redis marker/storage layer and the `live_check` dispatcher
/// that fans out to each provider's `check_cached`.
pub mod alldebrid;
pub mod cache;
pub mod debridlink;
pub mod easydebrid;
pub mod metadata_update;
pub mod offcloud;
pub mod pikpak;
pub mod premiumize;
pub mod realdebrid;
pub mod seedr;
pub mod stremthru;
pub mod torbox;
pub mod transport;
