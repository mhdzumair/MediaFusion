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

pub use realdebrid::DownloadedTorrent;

use crate::providers::ProviderError;

/// Providers that expose a downloaded-torrent listing API.
pub fn supports_download_list(service: &str) -> bool {
    matches!(
        service,
        "realdebrid"
            | "torbox"
            | "alldebrid"
            | "debridlink"
            | "premiumize"
            | "offcloud"
            | "pikpak"
            | "seedr"
            | "stremthru"
    )
}

/// List all downloaded torrents for a debrid provider (single dispatch point).
pub async fn list_downloaded_torrents(
    http: &reqwest::Client,
    service: &str,
    token: &str,
) -> Result<Vec<DownloadedTorrent>, ProviderError> {
    match service {
        "realdebrid" => realdebrid::list_downloaded_torrents(http, token).await,
        "torbox" => torbox::list_downloaded_torrents(http, token).await,
        "alldebrid" => alldebrid::list_downloaded_torrents(http, token).await,
        "debridlink" => debridlink::list_downloaded_torrents(http, token).await,
        "premiumize" => premiumize::list_downloaded_torrents(http, token).await,
        "offcloud" => offcloud::list_downloaded_torrents(http, token).await,
        "pikpak" => pikpak::list_downloaded_torrents(http, token).await,
        "seedr" => seedr::list_downloaded_torrents(http, token).await,
        "stremthru" => stremthru::list_downloaded_torrents(http, token).await,
        // EasyDebrid is cache-only — no account-level torrent list endpoint exists.
        "easydebrid" => Ok(vec![]),
        other => {
            tracing::debug!("list_downloaded_torrents: no list API for '{other}'");
            Ok(vec![])
        }
    }
}

/// Return lowercased info_hashes for all downloaded torrents in the provider account.
pub async fn list_downloaded_hashes(
    http: &reqwest::Client,
    service: &str,
    token: &str,
) -> Result<Vec<String>, ProviderError> {
    Ok(list_downloaded_torrents(http, service, token)
        .await?
        .into_iter()
        .map(|t| t.info_hash)
        .collect())
}
