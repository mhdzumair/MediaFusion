//! Torrin streaming provider.
//!
//! Torrin (<https://torrin.app>) is a debrid + streaming service that implements
//! the StremThru store API, so this module is a thin wrapper over [`stremthru`]:
//! it pins the base URL to Torrin's API and passes the user's Torrin key
//! (`tr_...`) as store-level auth. All request/response handling, retries and
//! file selection are reused from the StremThru implementation.

use serde_json::json;

use crate::providers::ProviderError;
use crate::providers::response_json;
use crate::providers::torrents::transport::MediaFlowForward;

use super::DownloadedTorrent;
use super::stremthru;

const BASE_URL: &str = "https://api.torrin.app";
const USER_AGENT: &str = "MediaFusion";

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

/// Add the magnet to Torrin and return its store status. Content already in
/// Torrin's R2 comes back `downloaded` instantly; anything else is now being
/// pulled server-side. Uses Torrin's native bearer auth (which it accepts
/// alongside the StremThru store headers).
async fn add_and_status(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
    announce_list: &[String],
) -> Result<String, ProviderError> {
    let trackers = announce_list
        .iter()
        .map(|t| format!("tr={}", urlencoding::encode(t)))
        .collect::<Vec<_>>()
        .join("&");
    let magnet = format!("magnet:?xt=urn:btih:{info_hash}&{trackers}");

    let resp = http
        .post(format!("{BASE_URL}/v0/store/magnets"))
        .header("User-Agent", USER_AGENT)
        .header("Authorization", format!("Bearer {token}"))
        .json(&json!({ "magnet": magnet }))
        .send()
        .await?;

    let body = response_json(resp, "torrin add magnet").await?;
    Ok(body
        .get("data")
        .and_then(|d| d.get("status"))
        .and_then(|s| s.as_str())
        .unwrap_or_default()
        .to_string())
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
    // Torrin has no shared instant cache (unlike RD/TB): a hash is either already
    // in R2 (`downloaded`) or Torrin must pull the whole file (minutes), so
    // blocking a playback request waiting for it is pointless. The add kicks off
    // the download; resolve immediately when cached, otherwise hand back a
    // "downloading" placeholder and let the user retry once Torrin finishes.
    let status = add_and_status(http, token, info_hash, announce_list).await?;

    if status.eq_ignore_ascii_case("downloaded") {
        return stremthru::get_video_url(
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
        .await;
    }

    Err(ProviderError::api(
        format!(
            "Torrin is downloading this title (status: {status}). Play again in a few minutes."
        ),
        "torrent_downloading.mp4",
    ))
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
