//! Torrin streaming provider.
//!
//! Torrin (<https://torrin.app>) is a debrid + streaming service that implements
//! the StremThru store API, so this module is a thin wrapper over [`stremthru`]:
//! it pins the base URL to Torrin's API and passes the user's Torrin key
//! (`tr_...`) as store-level auth. All request/response handling, retries and
//! file selection are reused from the StremThru implementation.

use crate::providers::ProviderError;
use crate::providers::torrents::transport::MediaFlowForward;

use super::DownloadedTorrent;
use super::stremthru;

const BASE_URL: &str = "https://api.torrin.app";

/// Rewrite the bare Torrin key into a StremThru store token pinned to Torrin's
/// API: `{url}|{store_name}:{store_token}`. The store name is arbitrary (Torrin
/// is the store itself); the key travels as the store authorization.
fn stremthru_token(token: &str) -> String {
    format!("{BASE_URL}|torrin:{token}")
}

pub async fn validate_credentials(
    http: &reqwest::Client,
    token: &str,
) -> Result<(), ProviderError> {
    stremthru::validate_credentials(http, &stremthru_token(token)).await
}

pub async fn list_downloaded_torrents(
    http: &reqwest::Client,
    token: &str,
) -> Result<Vec<DownloadedTorrent>, ProviderError> {
    stremthru::list_downloaded_torrents(http, &stremthru_token(token)).await
}

#[allow(clippy::too_many_arguments)]
pub async fn get_video_url(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
    announce_list: &[String],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    stremthru::get_video_url(
        http,
        &stremthru_token(token),
        info_hash,
        announce_list,
        filename,
        file_index,
        season,
        episode,
        user_ip,
        forward,
    )
    .await
}

pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    stremthru::delete_all_torrents(http, &stremthru_token(token)).await
}

pub async fn check_cached(
    http: &reqwest::Client,
    token: &str,
    hashes: &[String],
    media_id: i32,
) -> Vec<String> {
    stremthru::check_cached(http, &stremthru_token(token), hashes, media_id).await
}
