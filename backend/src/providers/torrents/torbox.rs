/// TorBox streaming provider.
///
/// Token format: raw API token used directly as Bearer.
use serde_json::Value;
use std::sync::OnceLock;

use crate::providers::{
    torrents::transport::{encode_form_body, MediaFlowForward},
    ProviderError,
};

const BASE_URL: &str = "https://api.torbox.app/v1/api";

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
///
/// Returns the index into `files` (not a file_id).
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

// ─── Error mapping ────────────────────────────────────────────────────────────

fn map_torbox_error(error_code: &str) -> Option<(&'static str, &'static str)> {
    Some(match error_code {
        "BAD_TOKEN" | "AUTH_ERROR" => ("Invalid Torbox token", "invalid_token.mp4"),
        "DOWNLOAD_TOO_LARGE" => ("Download size too large", "not_enough_space.mp4"),
        "ACTIVE_LIMIT" | "MONTHLY_LIMIT" => ("Download limit exceeded", "daily_download_limit.mp4"),
        "PLAN_RESTRICTED_FEATURE" => ("Need premium TorBox account", "need_premium.mp4"),
        _ => return None,
    })
}

fn check_torbox_error(body: &Value) -> Result<(), ProviderError> {
    if let Some(error_code) = body.get("error").and_then(|v| v.as_str()) {
        if let Some((label, file)) = map_torbox_error(error_code) {
            return Err(ProviderError::api(label, file));
        }
        let detail = body
            .get("detail")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error");
        return Err(ProviderError::api(
            format!("TorBox error {error_code}: {detail}"),
            "api_error.mp4",
        ));
    }
    Ok(())
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

async fn tb_get(
    http: &reqwest::Client,
    token: &str,
    url: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let resp = if let Some(fwd) = forward {
        fwd.get(http, url, token).await?
    } else {
        http.get(url).bearer_auth(token).send().await?
    };
    let body: Value = resp.json().await?;
    check_torbox_error(&body)?;
    Ok(body)
}

async fn tb_post_form(
    http: &reqwest::Client,
    token: &str,
    url: &str,
    form: &[(&str, &str)],
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let resp = if let Some(fwd) = forward {
        let body_str = encode_form_body(form);
        fwd.post_form(http, url, token, body_str).await?
    } else {
        http.post(url).bearer_auth(token).form(form).send().await?
    };
    let body: Value = resp.json().await?;
    check_torbox_error(&body)?;
    Ok(body)
}

async fn tb_post_json(
    http: &reqwest::Client,
    token: &str,
    url: &str,
    payload: &Value,
) -> Result<Value, ProviderError> {
    let resp = http
        .post(url)
        .bearer_auth(token)
        .json(payload)
        .send()
        .await?;
    let body: Value = resp.json().await?;
    check_torbox_error(&body)?;
    Ok(body)
}

// ─── TorBox API operations ────────────────────────────────────────────────────

async fn get_mylist(
    http: &reqwest::Client,
    token: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}/torrents/mylist?bypass_cache=true");
    tb_get(http, token, &url, forward).await
}

fn find_torrent_in_list(list: &Value, info_hash: &str) -> Option<Value> {
    let arr = list.get("data").and_then(|d| d.as_array())?;
    let hash_lower = info_hash.to_lowercase();
    arr.iter()
        .find(|t| {
            t.get("hash")
                .and_then(|v| v.as_str())
                .map(|h| h.to_lowercase() == hash_lower)
                .unwrap_or(false)
        })
        .cloned()
}

async fn get_queued(
    http: &reqwest::Client,
    token: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}/queued/getqueued?type=torrent&bypass_cache=true");
    tb_get(http, token, &url, forward).await
}

fn is_torrent_queued(queued: &Value, info_hash: &str) -> bool {
    let hash_lower = info_hash.to_lowercase();
    queued
        .get("data")
        .and_then(|d| d.as_array())
        .map(|arr| {
            arr.iter().any(|item| {
                item.get("hash")
                    .and_then(|v| v.as_str())
                    .map(|h| h.to_lowercase() == hash_lower)
                    .unwrap_or(false)
            })
        })
        .unwrap_or(false)
}

async fn create_torrent(
    http: &reqwest::Client,
    token: &str,
    magnet: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}/torrents/createtorrent");
    tb_post_form(http, token, &url, &[("magnet", magnet)], forward).await
}

async fn request_download_link(
    http: &reqwest::Client,
    token: &str,
    torrent_id: i64,
    file_id: i64,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let mut url = format!(
        "{BASE_URL}/torrents/requestdl?token={token}&torrent_id={torrent_id}&file_id={file_id}"
    );
    if let Some(ip) = user_ip {
        url.push_str(&format!("&user_ip={}", urlencoding::encode(ip)));
    }
    let body = tb_get(http, token, &url, forward).await?;
    body.get("data")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| {
            ProviderError::api("Missing download URL in TorBox response", "api_error.mp4")
        })
}

fn extract_files_from_torrent(torrent: &Value) -> Vec<(i64, String, i64)> {
    // Returns (file_id, name, size)
    torrent
        .get("files")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|f| {
                    let id = f.get("id").and_then(|v| v.as_i64())?;
                    let name = f
                        .get("short_name")
                        .or_else(|| f.get("name"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let size = f.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
                    Some((id, name, size))
                })
                .collect()
        })
        .unwrap_or_default()
}

#[allow(clippy::too_many_arguments)]
async fn build_download_link_from_torrent(
    http: &reqwest::Client,
    token: &str,
    torrent: &Value,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let torrent_id = torrent.get("id").and_then(|v| v.as_i64()).ok_or_else(|| {
        ProviderError::api("Missing torrent id in TorBox response", "api_error.mp4")
    })?;

    let raw_files = extract_files_from_torrent(torrent);
    if raw_files.is_empty() {
        return Err(ProviderError::api(
            "No files found in TorBox torrent",
            "torrent_not_downloaded.mp4",
        ));
    }

    let name_size: Vec<(String, i64)> = raw_files
        .iter()
        .map(|(_, name, size)| (name.clone(), *size))
        .collect();

    let idx = select_video_file(&name_size, filename, file_index, season, episode);
    let file_id = raw_files[idx].0;

    request_download_link(http, token, torrent_id, file_id, user_ip, forward).await
}

// ─── Public entry points ──────────────────────────────────────────────────────

/// Resolve a direct video URL from TorBox for the given torrent.
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
    let magnet = format!(
        "magnet:?xt=urn:btih:{}&{}",
        info_hash,
        announce_list
            .iter()
            .map(|t| format!("tr={}", urlencoding::encode(t)))
            .collect::<Vec<_>>()
            .join("&")
    );

    // Check if torrent already exists and is ready
    let mylist = get_mylist(http, token, forward).await?;
    if let Some(torrent) = find_torrent_in_list(&mylist, info_hash) {
        let finished = torrent
            .get("download_finished")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let present = torrent
            .get("download_present")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        if finished && present {
            return build_download_link_from_torrent(
                http, token, &torrent, filename, file_index, season, episode, user_ip, forward,
            )
            .await;
        }
        // Torrent exists but not ready yet
        return Err(ProviderError::api(
            "Torrent is not yet downloaded on TorBox",
            "torrent_not_downloaded.mp4",
        ));
    }

    // Check queued list before creating
    let queued = get_queued(http, token, forward)
        .await
        .unwrap_or(Value::Null);
    if is_torrent_queued(&queued, info_hash) {
        return Err(ProviderError::api(
            "Torrent is queued on TorBox but not yet downloaded",
            "torrent_not_downloaded.mp4",
        ));
    }

    // Add the torrent
    let create_resp = create_torrent(http, token, &magnet, forward).await?;
    let detail = create_resp
        .get("detail")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    if detail.contains("Found Cached") {
        // Re-check mylist — should now be present
        let mylist2 = get_mylist(http, token, forward).await?;
        if let Some(torrent) = find_torrent_in_list(&mylist2, info_hash) {
            let finished = torrent
                .get("download_finished")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let present = torrent
                .get("download_present")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            if finished && present {
                return build_download_link_from_torrent(
                    http, token, &torrent, filename, file_index, season, episode, user_ip, forward,
                )
                .await;
            }
        }
    }

    Err(ProviderError::api(
        "Torrent added to TorBox but not yet downloaded",
        "torrent_not_downloaded.mp4",
    ))
}

/// Delete the torrent matching `info_hash` from TorBox.
/// Returns `true` if found and deleted, `false` if not found.
pub async fn delete_torrent_by_hash(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
) -> Result<bool, ProviderError> {
    let mylist = get_mylist(http, token, None).await?;
    match find_torrent_in_list(&mylist, info_hash) {
        None => Ok(false),
        Some(torrent) => {
            if let Some(id) = torrent.get("id").and_then(|v| v.as_i64()) {
                let url = format!("{BASE_URL}/torrents/controltorrent");
                let payload = serde_json::json!({ "torrent_id": id, "operation": "delete" });
                tb_post_json(http, token, &url, &payload).await.ok();
            }
            Ok(true)
        }
    }
}

/// Delete ALL torrents from the user's TorBox account.
pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    let mylist = get_mylist(http, token, None).await?;
    let torrents = mylist
        .get("data")
        .and_then(|d| d.as_array())
        .cloned()
        .unwrap_or_default();

    for torrent in torrents {
        if let Some(id) = torrent.get("id").and_then(|v| v.as_i64()) {
            let url = format!("{BASE_URL}/torrents/controltorrent");
            let payload = serde_json::json!({
                "torrent_id": id,
                "operation": "delete"
            });
            tb_post_json(http, token, &url, &payload).await.ok();
        }
    }

    Ok(())
}

// ─── Debrid cache check ───────────────────────────────────────────────────────

/// Check which hashes are instantly cached on TorBox.
///
/// Uses `format=object` — only cached hashes appear as object keys in `data`.
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    const CHUNK: usize = 80;
    let mut cached = Vec::new();

    for chunk in hashes.chunks(CHUNK) {
        let params: Vec<(&str, &str)> = chunk.iter().map(|h| ("hash", h.as_str())).collect();
        let resp = match http
            .get(format!("{BASE_URL}/torrents/checkcached"))
            .bearer_auth(token)
            .query(&[("format", "object")])
            .query(&params)
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!("torbox checkcached: {e}");
                continue;
            }
        };
        let body: Value = match resp.json().await {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!("torbox checkcached json: {e}");
                continue;
            }
        };
        match body.get("data") {
            Some(Value::Object(obj)) => {
                for hash in obj.keys() {
                    cached.push(hash.clone());
                }
            }
            Some(Value::Array(arr)) => {
                for v in arr {
                    if let Some(h) = v.as_str() {
                        cached.push(h.to_string());
                    }
                }
            }
            _ => {}
        }
    }
    cached
}
