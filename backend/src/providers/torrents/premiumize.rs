/// Premiumize streaming provider.
///
/// Token format:
///   - Base64-encoded OAuth token → decode to JSON `{"access_token": "..."}` → Bearer
///   - Private API key → appended as `?apikey={token}` to every request
use base64::{engine::general_purpose::URL_SAFE_NO_PAD as B64, Engine as _};
use serde_json::Value;

use crate::providers::{
    torrents::transport::{append_query, encode_form_body, MediaFlowForward},
    ProviderError,
};

const BASE_URL: &str = "https://www.premiumize.me/api";

// ─── Token detection ──────────────────────────────────────────────────────────

enum TokenKind {
    Bearer(String),
    ApiKey(String),
}

fn decode_token(token: &str) -> TokenKind {
    if let Ok(bytes) = B64.decode(token) {
        if let Ok(s) = std::str::from_utf8(&bytes) {
            if let Ok(v) = serde_json::from_str::<Value>(s) {
                if let Some(at) = v.get("access_token").and_then(|x| x.as_str()) {
                    return TokenKind::Bearer(at.to_string());
                }
            }
        }
    }
    TokenKind::ApiKey(token.to_string())
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

/// Build a GET request, injecting auth.
async fn pm_get(
    http: &reqwest::Client,
    kind: &TokenKind,
    path: &str,
    extra_query: &[(&str, &str)],
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = if let Some(fwd) = forward {
        match kind {
            TokenKind::Bearer(t) => {
                let dest = if extra_query.is_empty() {
                    url
                } else {
                    append_query(&url, extra_query)
                };
                fwd.get(http, &dest, t).await?
            }
            TokenKind::ApiKey(k) => {
                // Embed apikey in URL; no Bearer header needed
                let mut all_params: Vec<(&str, &str)> = vec![("apikey", k.as_str())];
                all_params.extend_from_slice(extra_query);
                let dest = append_query(&url, &all_params);
                fwd.get_no_auth(http, &dest).await?
            }
        }
    } else {
        let mut req = match kind {
            TokenKind::Bearer(t) => http.get(&url).bearer_auth(t),
            TokenKind::ApiKey(k) => http.get(&url).query(&[("apikey", k.as_str())]),
        };
        if !extra_query.is_empty() {
            req = req.query(extra_query);
        }
        req.send().await?
    };
    check_status_code(resp.status())?;
    let body: Value = resp.json().await?;
    check_pm_error(&body)?;
    Ok(body)
}

/// Build a POST (form) request, injecting auth.
async fn pm_post_form(
    http: &reqwest::Client,
    kind: &TokenKind,
    path: &str,
    fields: Vec<(String, String)>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = if let Some(fwd) = forward {
        match kind {
            TokenKind::Bearer(t) => {
                let form_ref: Vec<(&str, &str)> = fields
                    .iter()
                    .map(|(k, v)| (k.as_str(), v.as_str()))
                    .collect();
                let body_str = encode_form_body(&form_ref);
                fwd.post_form(http, &url, t, body_str).await?
            }
            TokenKind::ApiKey(k) => {
                // Embed apikey in URL; body carries only the data fields
                let dest = append_query(&url, &[("apikey", k.as_str())]);
                let form_ref: Vec<(&str, &str)> = fields
                    .iter()
                    .map(|(k, v)| (k.as_str(), v.as_str()))
                    .collect();
                let body_str = encode_form_body(&form_ref);
                fwd.post_form_no_auth(http, &dest, body_str).await?
            }
        }
    } else {
        let mut form = fields;
        if let TokenKind::ApiKey(k) = kind {
            form.push(("apikey".to_string(), k.clone()));
        }
        let form_ref: Vec<(&str, &str)> =
            form.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
        let mut req = http.post(&url).form(&form_ref);
        if let TokenKind::Bearer(t) = kind {
            req = http.post(&url).bearer_auth(t).form(&form_ref);
        }
        req.send().await?
    };
    check_status_code(resp.status())?;
    let body: Value = resp.json().await?;
    check_pm_error(&body)?;
    Ok(body)
}

fn check_status_code(status: reqwest::StatusCode) -> Result<(), ProviderError> {
    if status == 403 {
        return Err(ProviderError::api(
            "Invalid Premiumize token",
            "invalid_token.mp4",
        ));
    }
    Ok(())
}

fn check_pm_error(body: &Value) -> Result<(), ProviderError> {
    if let Some(status) = body.get("status").and_then(|v| v.as_str()) {
        if status != "success" {
            let msg = body
                .get("message")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown error");
            return Err(ProviderError::api(
                format!("Premiumize API error: {msg}"),
                "transfer_error.mp4",
            ));
        }
    }
    Ok(())
}

// ─── File selection helper ────────────────────────────────────────────────────

/// Select a file index from a list of `(name, size)` pairs.
///
/// Priority: file_index → filename match → season/episode → largest video.
fn select_video_file(
    files: &[(String, i64)],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> usize {
    let video_exts = ["mkv", "mp4", "avi", "webm", "mov", "flv", "m4v", "wmv"];

    // 1. Explicit index
    if let Some(fi) = file_index {
        if fi >= 0 && (fi as usize) < files.len() {
            return fi as usize;
        }
    }

    // Collect video file indices
    let video_indices: Vec<usize> = files
        .iter()
        .enumerate()
        .filter_map(|(i, (name, _))| {
            let ext = std::path::Path::new(name.as_str())
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("")
                .to_lowercase();
            if video_exts.contains(&ext.as_str()) {
                Some(i)
            } else {
                None
            }
        })
        .collect();

    // 2. Filename substring match
    if let Some(fname) = filename {
        let lower = fname.to_lowercase();
        for &i in &video_indices {
            if files[i].0.to_lowercase().contains(&lower) {
                return i;
            }
        }
    }

    // 3. Season/episode regex match
    if let (Some(s), Some(e)) = (season, episode) {
        // compile once per call — acceptable for the hot path
        let re1 = regex::Regex::new(r"[Ss](\d+)[Ee](\d+)").unwrap();
        let re2 = regex::Regex::new(r"(\d+)x(\d+)").unwrap();
        for &i in &video_indices {
            let name = &files[i].0;
            if let Some(caps) = re1.captures(name).or_else(|| re2.captures(name)) {
                let cs: i32 = caps[1].parse().unwrap_or(-1);
                let ce: i32 = caps[2].parse().unwrap_or(-1);
                if cs == s && ce == e {
                    return i;
                }
            }
        }
    }

    // 4. Largest video
    video_indices
        .iter()
        .max_by_key(|&&i| files[i].1)
        .copied()
        .unwrap_or(0)
}

// ─── Premiumize API operations ────────────────────────────────────────────────

async fn check_cache(
    http: &reqwest::Client,
    kind: &TokenKind,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<bool, ProviderError> {
    let body = pm_get(
        http,
        kind,
        "/cache/check",
        &[("items[]", info_hash)],
        forward,
    )
    .await?;
    Ok(body
        .get("response")
        .and_then(|v| v.as_array())
        .and_then(|a| a.first())
        .and_then(|v| v.as_bool())
        .unwrap_or(false))
}

async fn direct_download(
    http: &reqwest::Client,
    kind: &TokenKind,
    magnet: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    pm_post_form(
        http,
        kind,
        "/transfer/directdl",
        vec![("src".to_string(), magnet.to_string())],
        forward,
    )
    .await
}

fn select_from_directdl_content(
    content: &[Value],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<String> {
    let files: Vec<(String, i64)> = content
        .iter()
        .map(|f| {
            let path = f
                .get("path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let size = f.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
            (path, size)
        })
        .collect();

    if files.is_empty() {
        return None;
    }

    let idx = select_video_file(&files, filename, file_index, season, episode);
    let entry = &content[idx];
    entry
        .get("stream_link")
        .or_else(|| entry.get("link"))
        .and_then(|v| v.as_str())
        .map(str::to_string)
}

async fn get_or_create_folder(
    http: &reqwest::Client,
    kind: &TokenKind,
    name: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    // List root folders
    let body = pm_get(http, kind, "/folder/list", &[], forward).await?;
    if let Some(folders) = body.get("content").and_then(|v| v.as_array()) {
        for f in folders {
            if f.get("name").and_then(|v| v.as_str()) == Some(name) {
                if let Some(id) = f.get("id").and_then(|v| v.as_str()) {
                    return Ok(id.to_string());
                }
            }
        }
    }
    // Create new folder
    let resp = pm_post_form(
        http,
        kind,
        "/folder/create",
        vec![("name".to_string(), name.to_string())],
        forward,
    )
    .await?;
    resp.get("id")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| {
            ProviderError::api("Failed to create Premiumize folder", "transfer_error.mp4")
        })
}

async fn create_transfer(
    http: &reqwest::Client,
    kind: &TokenKind,
    magnet: &str,
    folder_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    pm_post_form(
        http,
        kind,
        "/transfer/create",
        vec![
            ("src".to_string(), magnet.to_string()),
            ("folder_id".to_string(), folder_id.to_string()),
        ],
        forward,
    )
    .await
}

async fn wait_for_transfer(
    http: &reqwest::Client,
    kind: &TokenKind,
    transfer_id: &str,
    max_retries: u32,
    retry_secs: u64,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    for attempt in 0..max_retries {
        let body = pm_get(http, kind, "/transfer/list", &[], forward).await?;
        if let Some(transfers) = body.get("transfers").and_then(|v| v.as_array()) {
            for t in transfers {
                if t.get("id").and_then(|v| v.as_str()) == Some(transfer_id) {
                    let status = t.get("status").and_then(|v| v.as_str()).unwrap_or("");
                    if status == "finished" {
                        return Ok(());
                    }
                    // Error states
                    if status == "error" || status == "deleted" {
                        let msg = t
                            .get("message")
                            .and_then(|v| v.as_str())
                            .unwrap_or("Transfer error");
                        return Err(ProviderError::api(
                            format!("Premiumize transfer error: {msg}"),
                            "transfer_error.mp4",
                        ));
                    }
                    break;
                }
            }
        }
        if attempt + 1 < max_retries {
            tokio::time::sleep(tokio::time::Duration::from_secs(retry_secs)).await;
        }
    }
    Err(ProviderError::api(
        format!("Premiumize transfer did not finish after {max_retries} retries"),
        "torrent_not_downloaded.mp4",
    ))
}

async fn get_folder_contents(
    http: &reqwest::Client,
    kind: &TokenKind,
    folder_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<Value>, ProviderError> {
    let body = pm_get(http, kind, "/folder/list", &[("id", folder_id)], forward).await?;
    Ok(body
        .get("content")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default())
}

fn select_from_folder_content(
    content: &[Value],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<String> {
    // Filter to video files by mime_type
    let video_files: Vec<&Value> = content
        .iter()
        .filter(|f| {
            f.get("mime_type")
                .and_then(|v| v.as_str())
                .map(|m| m.contains("video"))
                .unwrap_or(false)
        })
        .collect();

    if video_files.is_empty() {
        return None;
    }

    let pairs: Vec<(String, i64)> = video_files
        .iter()
        .map(|f| {
            let name = f
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let size = f.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
            (name, size)
        })
        .collect();

    let idx = select_video_file(&pairs, filename, file_index, season, episode);
    video_files
        .get(idx)
        .and_then(|f| f.get("link"))
        .and_then(|v| v.as_str())
        .map(str::to_string)
}

// ─── Public entry points ──────────────────────────────────────────────────────

/// Resolve a direct video URL from Premiumize for the given torrent.
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
    const RETRY_SECS: u64 = 5;

    let kind = decode_token(token);

    // Build magnet
    let trackers: String = announce_list
        .iter()
        .map(|t| format!("&tr={}", urlencoding::encode(t)))
        .collect();
    let magnet = format!("magnet:?xt=urn:btih:{info_hash}{trackers}");

    // Check instant availability
    let cached = check_cache(http, &kind, info_hash, forward).await?;

    if cached {
        let body = direct_download(http, &kind, &magnet, forward).await?;
        if let Some(content) = body.get("content").and_then(|v| v.as_array()) {
            if let Some(url) =
                select_from_directdl_content(content, filename, file_index, season, episode)
            {
                return Ok(url);
            }
        }
        return Err(ProviderError::api(
            "No video file found in Premiumize direct download",
            "torrent_not_downloaded.mp4",
        ));
    }

    // Not cached — use transfer flow
    let folder_id = get_or_create_folder(http, &kind, info_hash, forward).await?;
    let transfer_resp = create_transfer(http, &kind, &magnet, &folder_id, forward).await?;

    // If the transfer already has content (immediate), skip waiting
    if let Some(content) = transfer_resp.get("content").and_then(|v| v.as_array()) {
        if let Some(url) =
            select_from_directdl_content(content, filename, file_index, season, episode)
        {
            return Ok(url);
        }
    }

    let transfer_id = transfer_resp
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ProviderError::api("No transfer id from Premiumize", "transfer_error.mp4"))?
        .to_string();

    wait_for_transfer(http, &kind, &transfer_id, MAX_RETRIES, RETRY_SECS, forward).await?;

    let content = get_folder_contents(http, &kind, &folder_id, forward).await?;
    select_from_folder_content(&content, filename, file_index, season, episode).ok_or_else(|| {
        ProviderError::api(
            "No video file found in Premiumize folder",
            "torrent_not_downloaded.mp4",
        )
    })
}

/// Delete the transfer matching `info_hash` from Premiumize.
/// Lists `/transfer/list` to find the transfer by its `hash` field, then calls `/transfer/delete`.
/// Returns `true` if found and deleted, `false` if not found.
pub async fn delete_torrent_by_hash(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
) -> Result<bool, ProviderError> {
    let kind = decode_token(token);
    let body = pm_get(http, &kind, "/transfer/list", &[], None).await?;
    let hash_lower = info_hash.to_lowercase();

    let transfer_id = body
        .get("transfers")
        .and_then(|v| v.as_array())
        .and_then(|arr| {
            arr.iter().find(|t| {
                t.get("src")
                    .or_else(|| t.get("hash"))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_lowercase().contains(&hash_lower))
                    .unwrap_or(false)
            })
        })
        .and_then(|t| t.get("id").and_then(|v| v.as_str()))
        .map(str::to_string);

    match transfer_id {
        None => Ok(false),
        Some(id) => {
            pm_post_form(
                http,
                &kind,
                "/transfer/delete",
                vec![("id".to_string(), id)],
                None,
            )
            .await
            .ok();
            Ok(true)
        }
    }
}

/// Delete ALL folders (and their contents) from the Premiumize account.
pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    let kind = decode_token(token);

    let body = pm_get(http, &kind, "/folder/list", &[], None).await?;
    let folders = body
        .get("content")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    for folder in &folders {
        if let Some(id) = folder.get("id").and_then(|v| v.as_str()) {
            pm_post_form(
                http,
                &kind,
                "/folder/delete",
                vec![("id".to_string(), id.to_string())],
                None,
            )
            .await
            .ok();
        }
    }

    Ok(())
}

// ─── Debrid cache check ───────────────────────────────────────────────────────

/// Check which hashes are cached on Premiumize.
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    let kind = decode_token(token);
    let params: Vec<(&str, &str)> = hashes.iter().map(|h| ("items[]", h.as_str())).collect();
    let body = match pm_get(http, &kind, "/cache/check", &params, None).await {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("premiumize cache/check: {e}");
            return vec![];
        }
    };
    if body.get("status").and_then(|v| v.as_str()) != Some("success") {
        return vec![];
    }
    let responses = match body.get("response").and_then(|v| v.as_array()) {
        Some(a) => a.clone(),
        None => return vec![],
    };
    hashes
        .iter()
        .zip(responses.iter())
        .filter_map(|(h, v)| {
            if v.as_bool().unwrap_or(false) {
                Some(h.clone())
            } else {
                None
            }
        })
        .collect()
}
