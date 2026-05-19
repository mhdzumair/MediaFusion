/// OffCloud streaming provider.
///
/// Token format: raw API key used as Bearer token AND `key=` query parameter.
use serde_json::Value;
use std::sync::OnceLock;

use crate::providers::{
    torrents::transport::{append_query, encode_form_body, MediaFlowForward},
    ProviderError,
};

const BASE_URL: &str = "https://offcloud.com";

// ─── Regex helpers ────────────────────────────────────────────────────────────

static SE_REGEX: OnceLock<regex::Regex> = OnceLock::new();
static ALT_REGEX: OnceLock<regex::Regex> = OnceLock::new();

fn se_regex() -> &'static regex::Regex {
    SE_REGEX.get_or_init(|| regex::Regex::new(r"[Ss](\d{1,2})[Ee](\d{1,2})").unwrap())
}

fn alt_regex() -> &'static regex::Regex {
    ALT_REGEX.get_or_init(|| regex::Regex::new(r"(\d{1,2})x(\d{1,2})").unwrap())
}

// ─── Video file selection helper ──────────────────────────────────────────────

/// Select the best-matching video file index from a list of (name, size) pairs.
fn select_video_file(
    files: &[(String, i64)],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> usize {
    if files.is_empty() {
        return 0;
    }

    // 1. Explicit file_index
    if let Some(fi) = file_index {
        if fi >= 0 && (fi as usize) < files.len() {
            return fi as usize;
        }
    }

    let video_exts = ["mkv", "mp4", "avi", "webm", "mov", "flv", "m4v", "wmv"];

    let video_indices: Vec<usize> = files
        .iter()
        .enumerate()
        .filter(|(_, (name, _))| {
            let ext = std::path::Path::new(name.as_str())
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("")
                .to_lowercase();
            video_exts.contains(&ext.as_str())
        })
        .map(|(i, _)| i)
        .collect();

    // 2. By filename
    if let Some(name) = filename {
        let name_lower = name.to_lowercase();
        if let Some(&idx) = video_indices
            .iter()
            .find(|&&i| files[i].0.to_lowercase().contains(&name_lower))
        {
            return idx;
        }
    }

    // 3. By season/episode
    if let (Some(s), Some(e)) = (season, episode) {
        for &idx in &video_indices {
            let lower = files[idx].0.to_lowercase();
            if let Some(caps) = se_regex().captures(&lower) {
                let fs: i32 = caps[1].parse().unwrap_or(-1);
                let fe: i32 = caps[2].parse().unwrap_or(-1);
                if fs == s && fe == e {
                    return idx;
                }
            }
            if let Some(caps) = alt_regex().captures(&lower) {
                let fs: i32 = caps[1].parse().unwrap_or(-1);
                let fe: i32 = caps[2].parse().unwrap_or(-1);
                if fs == s && fe == e {
                    return idx;
                }
            }
        }
    }

    // 4. Largest video file
    video_indices
        .iter()
        .copied()
        .max_by_key(|&i| files[i].1)
        .unwrap_or(0)
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

/// GET request with Bearer auth + `key=` query param.
async fn oc_get(
    http: &reqwest::Client,
    token: &str,
    path: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = if let Some(fwd) = forward {
        // Embed key= in destination URL; Bearer forwarded via h_authorization
        let dest = append_query(&url, &[("key", token)]);
        fwd.get(http, &dest, token).await?
    } else {
        http.get(&url)
            .bearer_auth(token)
            .query(&[("key", token)])
            .send()
            .await?
    };

    let status = resp.status();
    if status == reqwest::StatusCode::FORBIDDEN {
        return Err(ProviderError::api(
            "Invalid OffCloud API key",
            "invalid_token.mp4",
        ));
    }
    if status == reqwest::StatusCode::PAYMENT_REQUIRED {
        return Err(ProviderError::api(
            "Need premium OffCloud account",
            "need_premium.mp4",
        ));
    }
    if status == reqwest::StatusCode::TOO_MANY_REQUESTS {
        return Err(ProviderError::api(
            "OffCloud rate limit exceeded",
            "too_many_requests.mp4",
        ));
    }

    let body: Value = resp.json().await?;
    check_offcloud_error(&body)?;
    Ok(body)
}

/// POST request with form data; includes `key={token}` in form body.
async fn oc_post_form(
    http: &reqwest::Client,
    token: &str,
    path: &str,
    mut fields: Vec<(&str, String)>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    fields.push(("key", token.to_string()));

    let resp = if let Some(fwd) = forward {
        // Keep key in body; Bearer forwarded via h_authorization
        let form_ref: Vec<(&str, &str)> = fields.iter().map(|(k, v)| (*k, v.as_str())).collect();
        let body_str = encode_form_body(&form_ref);
        fwd.post_form(http, &url, token, body_str).await?
    } else {
        http.post(&url)
            .bearer_auth(token)
            .form(&fields)
            .send()
            .await?
    };

    let status = resp.status();
    if status == reqwest::StatusCode::FORBIDDEN {
        return Err(ProviderError::api(
            "Invalid OffCloud API key",
            "invalid_token.mp4",
        ));
    }
    if status == reqwest::StatusCode::PAYMENT_REQUIRED {
        return Err(ProviderError::api(
            "Need premium OffCloud account",
            "need_premium.mp4",
        ));
    }
    if status == reqwest::StatusCode::TOO_MANY_REQUESTS {
        return Err(ProviderError::api(
            "OffCloud rate limit exceeded",
            "too_many_requests.mp4",
        ));
    }

    let body: Value = resp.json().await?;
    check_offcloud_error(&body)?;
    Ok(body)
}

fn check_offcloud_error(body: &Value) -> Result<(), ProviderError> {
    // Check for "not_available" anywhere in the response
    if let Some(s) = body.as_str() {
        if s.contains("not_available") {
            return Err(ProviderError::api(
                "Need premium OffCloud account",
                "need_premium.mp4",
            ));
        }
    }
    if let Some(obj) = body.as_object() {
        for (_, v) in obj {
            if v.as_str()
                .map(|s| s.contains("not_available"))
                .unwrap_or(false)
            {
                return Err(ProviderError::api(
                    "Need premium OffCloud account",
                    "need_premium.mp4",
                ));
            }
        }
    }
    if let Some(error) = body.get("error") {
        let msg = error.as_str().unwrap_or("Unknown OffCloud error");
        if msg.contains("not_available") {
            return Err(ProviderError::api(
                "Need premium OffCloud account",
                "need_premium.mp4",
            ));
        }
        return Err(ProviderError::api(
            format!("OffCloud error: {msg}"),
            "api_error.mp4",
        ));
    }
    Ok(())
}

// ─── OffCloud API operations ──────────────────────────────────────────────────

/// Search cloud history for a torrent matching info_hash.
async fn find_in_history(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<Value>, ProviderError> {
    let body = oc_get(http, token, "/api/cloud/history", forward).await?;
    let hash_lower = info_hash.to_lowercase();

    let arr = match body.as_array() {
        Some(a) => a,
        None => return Ok(None),
    };

    Ok(arr
        .iter()
        .find(|item| {
            item.get("originalLink")
                .and_then(|v| v.as_str())
                .map(|s| s.to_lowercase().contains(&hash_lower))
                .unwrap_or(false)
        })
        .cloned())
}

/// Submit a magnet link to OffCloud cloud download.
async fn submit_magnet(
    http: &reqwest::Client,
    token: &str,
    magnet: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let body = oc_post_form(
        http,
        token,
        "/api/cloud",
        vec![("url", magnet.to_string())],
        forward,
    )
    .await?;

    body.get("requestId")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| {
            ProviderError::api(
                "Missing requestId in OffCloud submit response",
                "transfer_error.mp4",
            )
        })
}

/// Get the status of a cloud download request.
async fn get_torrent_status(
    http: &reqwest::Client,
    token: &str,
    request_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let body = oc_post_form(
        http,
        token,
        "/api/cloud/status",
        vec![("requestId", request_id.to_string())],
        forward,
    )
    .await?;

    // The API may return a `status` object directly or a `requests` array
    if body.get("status").is_some() {
        return Ok(body);
    }
    if let Some(arr) = body.get("requests").and_then(|v| v.as_array()) {
        if let Some(first) = arr.first() {
            return Ok(first.clone());
        }
    }
    // May be returned as the whole object
    Ok(body)
}

fn extract_status_str(info: &Value) -> &str {
    // May be in info["status"]["status"] or info["status"] string or info["requests"][0]["status"]
    if let Some(s) = info
        .get("status")
        .and_then(|v| v.as_object())
        .and_then(|obj| obj.get("status"))
        .and_then(|v| v.as_str())
    {
        return s;
    }
    if let Some(s) = info.get("status").and_then(|v| v.as_str()) {
        return s;
    }
    ""
}

/// Wait for a request to reach "downloaded" status.
async fn wait_for_downloaded(
    http: &reqwest::Client,
    token: &str,
    request_id: &str,
    max_retries: u32,
    retry_interval_secs: u64,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    for attempt in 0..max_retries {
        let info = get_torrent_status(http, token, request_id, forward).await?;
        let status = extract_status_str(&info);

        if status.eq_ignore_ascii_case("downloaded") {
            return Ok(info);
        }

        if matches!(status, "error" | "failed" | "dead") {
            return Err(ProviderError::api(
                format!("OffCloud download entered error status: {status}"),
                "transfer_error.mp4",
            ));
        }

        if attempt + 1 < max_retries {
            tokio::time::sleep(tokio::time::Duration::from_secs(retry_interval_secs)).await;
        }
    }
    Err(ProviderError::api(
        format!("OffCloud download did not reach 'downloaded' status after {max_retries} retries"),
        "torrent_not_downloaded.mp4",
    ))
}

/// Get the direct download URL(s) for a finished cloud request.
async fn explore_torrent(
    http: &reqwest::Client,
    token: &str,
    request_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<String>, ProviderError> {
    let body = oc_get(
        http,
        token,
        &format!("/api/cloud/explore/{request_id}"),
        forward,
    )
    .await?;

    match body {
        Value::Array(arr) => Ok(arr
            .into_iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect()),
        _ => Ok(vec![]),
    }
}

/// Try to resolve a single-file torrent URL directly from the status response,
/// without needing to call explore.
fn try_single_file_url(info: &Value, request_id: &str) -> Option<String> {
    // Direct `url` field
    if let Some(url) = info.get("url").and_then(|v| v.as_str()) {
        if !url.is_empty() {
            return Some(url.to_string());
        }
    }

    // isDirectory == false with server + fileName
    let is_dir = info
        .get("isDirectory")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    if !is_dir {
        if let (Some(server), Some(file_name)) = (
            info.get("server").and_then(|v| v.as_str()),
            info.get("fileName").and_then(|v| v.as_str()),
        ) {
            return Some(format!(
                "https://{server}.offcloud.com/cloud/download/{request_id}/{file_name}"
            ));
        }
    }

    None
}

/// Select a URL from the explore list by matching basename against filename/episode.
fn select_url_from_list(
    urls: &[String],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<String> {
    if urls.is_empty() {
        return None;
    }

    // Build (basename, dummy_size) pairs for select_video_file
    let pairs: Vec<(String, i64)> = urls
        .iter()
        .enumerate()
        .map(|(i, url)| {
            let basename = url
                .split('/')
                .next_back()
                .and_then(|s| {
                    // Strip query string if any
                    s.split('?').next()
                })
                .unwrap_or(url.as_str())
                .to_string();
            // Use index as a proxy size so select_video_file can rank them
            (basename, i as i64)
        })
        .collect();

    let idx = select_video_file(&pairs, filename, file_index, season, episode);
    urls.get(idx).cloned()
}

// ─── Public entry points ──────────────────────────────────────────────────────

/// Resolve a direct video URL from OffCloud for the given torrent.
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
    _user_ip: Option<&str>,
    forward: Option<&crate::providers::torrents::transport::MediaFlowForward>,
) -> Result<String, ProviderError> {
    const MAX_RETRIES: u32 = 5;
    const RETRY_INTERVAL: u64 = 5;

    let magnet = format!(
        "magnet:?xt=urn:btih:{}&{}",
        info_hash,
        announce_list
            .iter()
            .map(|t| format!("tr={}", urlencoding::encode(t)))
            .collect::<Vec<_>>()
            .join("&")
    );

    // Check history for existing download
    let request_id = match find_in_history(http, token, info_hash, forward).await? {
        Some(existing) => existing
            .get("requestId")
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .ok_or_else(|| {
                ProviderError::api(
                    "Missing requestId in OffCloud history entry",
                    "api_error.mp4",
                )
            })?,
        None => submit_magnet(http, token, &magnet, forward).await?,
    };

    // Wait for completion
    let torrent_info = wait_for_downloaded(
        http,
        token,
        &request_id,
        MAX_RETRIES,
        RETRY_INTERVAL,
        forward,
    )
    .await?;

    // Try single-file shortcut first
    if let Some(url) = try_single_file_url(&torrent_info, &request_id) {
        return Ok(url);
    }

    // Multi-file: explore and select
    let urls = explore_torrent(http, token, &request_id, forward).await?;

    select_url_from_list(&urls, filename, file_index, season, episode).ok_or_else(|| {
        ProviderError::api(
            "No matching video file found in OffCloud torrent",
            "torrent_not_downloaded.mp4",
        )
    })
}

/// Delete the cloud download matching `info_hash` from OffCloud.
/// Returns `true` if found and deleted, `false` if not found.
pub async fn delete_torrent_by_hash(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
) -> Result<bool, ProviderError> {
    match find_in_history(http, token, info_hash, None).await? {
        None => Ok(false),
        Some(item) => {
            if let Some(request_id) = item.get("requestId").and_then(|v| v.as_str()) {
                oc_get(http, token, &format!("/cloud/remove/{request_id}"), None)
                    .await
                    .ok();
            }
            Ok(true)
        }
    }
}

/// Delete ALL cloud downloads from the user's OffCloud account.
pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    let body = oc_get(http, token, "/api/cloud/history", None).await?;

    let items = match body.as_array() {
        Some(arr) => arr.clone(),
        None => return Ok(()),
    };

    for item in items {
        if let Some(request_id) = item.get("requestId").and_then(|v| v.as_str()) {
            // Best-effort; ignore individual errors
            oc_get(http, token, &format!("/cloud/remove/{request_id}"), None)
                .await
                .ok();
        }
    }

    Ok(())
}

// ─── Debrid cache check ───────────────────────────────────────────────────────

/// Check which hashes are cached on OffCloud (form-encoded POST /api/cache).
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    let url = "https://offcloud.com/api/cache";
    let form: Vec<(&str, &str)> = hashes.iter().map(|h| ("hashes", h.as_str())).collect();
    let resp = match http
        .post(url)
        .bearer_auth(token)
        .query(&[("key", token)])
        .form(&form)
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!("offcloud cache: {e}");
            return vec![];
        }
    };
    let body: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("offcloud cache json: {e}");
            return vec![];
        }
    };
    body.get("cachedItems")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}
