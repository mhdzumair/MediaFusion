/// EasyDebrid streaming provider.
///
/// Cache-only instant debrid — no waiting.
/// API base: https://easydebrid.com/api/v1
/// Auth: `Authorization: Bearer {token}`
/// IP forwarding: `X-Forwarded-For: {user_ip}` when user_ip is set.
use serde_json::Value;

use crate::providers::{
    ProviderError, file_selection::select_debrid_file_index, response_json,
    torrents::transport::MediaFlowForward,
};

const BASE_URL: &str = "https://easydebrid.com/api/v1";

// ─── File selection helper ─────────────────────────────────────────────────────

fn select_video_file(
    files: &[(String, i64)],
    release_name: &str,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> usize {
    select_debrid_file_index(
        files,
        release_name,
        filename,
        file_index,
        season,
        episode,
        None,
    )
}

// ─── HTTP helper ───────────────────────────────────────────────────────────────

/// Build a POST JSON request to EasyDebrid, setting auth and optional IP header.
async fn ed_post(
    http: &reqwest::Client,
    token: &str,
    path: &str,
    body: &Value,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<reqwest::Response, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    if let Some(fwd) = forward {
        // Route through MediaFlow; X-Forwarded-For would be stripped anyway — omit it
        let resp = fwd.post_json(http, &url, token, body.to_string()).await?;
        return Ok(resp);
    }
    let mut builder = http.post(&url).bearer_auth(token).json(body);
    if let Some(ip) = user_ip {
        builder = builder.header("X-Forwarded-For", ip);
    }
    let resp = builder.send().await?;
    Ok(resp)
}

// ─── Public entry point ────────────────────────────────────────────────────────

/// Resolve a direct video URL from EasyDebrid for the given torrent.
///
/// EasyDebrid is cache-only: if the torrent is not already cached,
/// we submit it for caching and return an error directing the user to try later.
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
    forward: Option<&crate::providers::torrents::transport::MediaFlowForward>,
) -> Result<String, ProviderError> {
    // Build magnet URI
    let trackers: String = announce_list
        .iter()
        .map(|t| format!("&tr={}", urlencoding::encode(t)))
        .collect();
    let magnet = format!("magnet:?xt=urn:btih:{info_hash}{trackers}");

    let request_body = serde_json::json!({ "url": magnet });
    // When routing through forward, X-Forwarded-For gets stripped — pass None for user_ip
    let effective_ip = if forward.is_some() { None } else { user_ip };

    // Step 1: POST /link/generate — attempt to get an instant link
    let generate_resp = ed_post(
        http,
        token,
        "/link/generate",
        &request_body,
        effective_ip,
        forward,
    )
    .await?;
    let status = generate_resp.status();

    if status.is_success() {
        let body: Value = response_json(generate_resp, "easydebrid link/generate").await?;

        // EasyDebrid returns { "link": "..." } on success
        if let Some(link) = body.get("link").and_then(|v| v.as_str())
            && !link.is_empty() {
                // EasyDebrid /link/generate returns a single direct link (not a file list),
                // so we return it directly. File selection is not applicable here since the
                // API resolves the best file server-side.
                //
                // If the API ever returns a list of links (future API change), the
                // select_video_file helper below can be used. For now we surface the link
                // as-is. The `_` prefixes below suppress unused-variable warnings while
                // keeping the helper reachable from the module.
                let _ = select_video_file;
                let _ = filename;
                let _ = file_index;
                let _ = season;
                let _ = episode;
                return Ok(link.to_string());
            }

        // Status 200 but no usable link — fall through to cache request
        tracing::debug!(
            info_hash,
            "EasyDebrid /link/generate returned 200 but no link; submitting for caching"
        );
    }

    // Step 2: POST /link/request — submit for caching and tell user to try later
    let _ = ed_post(
        http,
        token,
        "/link/request",
        &request_body,
        effective_ip,
        forward,
    )
    .await;

    Err(ProviderError::api(
        "Torrent is not yet cached on EasyDebrid; submitted for caching — try again later",
        "torrent_not_downloaded.mp4",
    ))
}

// ─── Downloaded torrent list (not supported) ──────────────────────────────────

// EasyDebrid is cache-only instant debrid. The API exposes /link/generate,
// /link/request, and /link/lookup but no account-level "list my downloads"
// endpoint, so watchlist catalogs cannot be populated from EasyDebrid.

// ─── Delete all torrents (no-op for EasyDebrid) ───────────────────────────────

/// EasyDebrid has no account-level deletion API — this is a no-op.
pub async fn delete_all_torrents(
    _http: &reqwest::Client,
    _token: &str,
) -> Result<(), ProviderError> {
    Ok(())
}

// ─── Debrid cache check ───────────────────────────────────────────────────────

/// Check which hashes are cached on EasyDebrid.
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    const CHUNK: usize = 50;
    let mut cached = Vec::new();
    for chunk in hashes.chunks(CHUNK) {
        let urls: Vec<String> = chunk
            .iter()
            .map(|h| format!("magnet:?xt=urn:btih:{h}"))
            .collect();
        let resp = match ed_post(
            http,
            token,
            "/link/lookup",
            &serde_json::json!({"urls": urls}),
            None,
            None,
        )
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!("easydebrid link/lookup: {e}");
                continue;
            }
        };
        let body: Value = match response_json(resp, "easydebrid link/lookup").await {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(arr) = body.get("cached").and_then(|v| v.as_array()) {
            for (hash, is_cached) in chunk.iter().zip(arr.iter()) {
                if is_cached.as_bool().unwrap_or(false) {
                    cached.push(hash.clone());
                }
            }
        }
    }
    cached
}
