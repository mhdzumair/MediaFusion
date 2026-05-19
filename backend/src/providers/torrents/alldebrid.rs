/// AllDebrid streaming provider.
///
/// API base: https://api.alldebrid.com/v4.1
/// All requests require `?agent=mediafusion` appended and a Bearer token.
use serde_json::Value;

use crate::providers::torrents::transport::{append_query, encode_form_body, MediaFlowForward};
use crate::providers::ProviderError;

const BASE_URL: &str = "https://api.alldebrid.com/v4.1";
const AGENT: &str = "mediafusion";

// ─── Error mapping ─────────────────────────────────────────────────────────────

fn map_ad_error(code: &str) -> Option<(&'static str, &'static str)> {
    Some(match code {
        "AUTH_BAD_APIKEY" => ("Invalid AllDebrid API key", "invalid_token.mp4"),
        "AUTH_BLOCKED" => ("API got blocked on AllDebrid", "alldebrid_api_blocked.mp4"),
        "MAGNET_MUST_BE_PREMIUM" => ("Torrent must be premium on AllDebrid", "need_premium.mp4"),
        "MAGNET_TOO_MANY_ACTIVE" | "MAGNET_TOO_MANY" => {
            ("Too many active torrents on AllDebrid", "torrent_limit.mp4")
        }
        "NO_SERVER" => ("Failed to add magnet to AllDebrid", "transfer_error.mp4"),
        _ => return None,
    })
}

fn check_ad_error(body: &Value) -> Result<(), ProviderError> {
    if body.get("status").and_then(|v| v.as_str()) == Some("error") {
        let code = body
            .get("error")
            .and_then(|e| e.get("code"))
            .and_then(|v| v.as_str())
            .unwrap_or("UNKNOWN");
        let message = body
            .get("error")
            .and_then(|e| e.get("message"))
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error");

        if let Some((label, file)) = map_ad_error(code) {
            return Err(ProviderError::api(format!("{label}: {message}"), file));
        }
        return Err(ProviderError::api(
            format!("AllDebrid error {code}: {message}"),
            "api_error.mp4",
        ));
    }
    Ok(())
}

// ─── HTTP helpers ──────────────────────────────────────────────────────────────

/// Build the base query string. Always includes agent; optionally includes ip.
fn build_query(user_ip: Option<&str>) -> Vec<(&'static str, String)> {
    let mut q = vec![("agent", AGENT.to_string())];
    if let Some(ip) = user_ip {
        q.push(("ip", ip.to_string()));
    }
    q
}

async fn ad_get(
    http: &reqwest::Client,
    token: &str,
    path: &str,
    extra_params: &[(&str, String)],
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let mut params = build_query(user_ip);
    params.extend_from_slice(extra_params);

    let resp = if let Some(fwd) = forward {
        let param_refs: Vec<(&str, &str)> = params.iter().map(|(k, v)| (*k, v.as_str())).collect();
        let dest = append_query(&url, &param_refs);
        fwd.get(http, &dest, token).await?
    } else {
        http.get(&url)
            .bearer_auth(token)
            .query(&params)
            .send()
            .await?
    };

    let body: Value = resp.json().await?;
    check_ad_error(&body)?;
    Ok(body)
}

async fn ad_post_form(
    http: &reqwest::Client,
    token: &str,
    path: &str,
    fields: &[(&str, String)],
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let query = build_query(user_ip);

    let mut form: Vec<(&str, String)> = fields.to_vec();
    // ip in the form body is also accepted for upload calls
    if let Some(ip) = user_ip {
        form.push(("ip", ip.to_string()));
    }

    let resp = if let Some(fwd) = forward {
        let query_refs: Vec<(&str, &str)> = query.iter().map(|(k, v)| (*k, v.as_str())).collect();
        let dest = append_query(&url, &query_refs);
        let form_refs: Vec<(&str, &str)> = form.iter().map(|(k, v)| (*k, v.as_str())).collect();
        let body_str = encode_form_body(&form_refs);
        fwd.post_form(http, &dest, token, body_str).await?
    } else {
        http.post(&url)
            .bearer_auth(token)
            .query(&query)
            .form(&form)
            .send()
            .await?
    };

    let body: Value = resp.json().await?;
    check_ad_error(&body)?;
    Ok(body)
}

// ─── File selection helper ─────────────────────────────────────────────────────

static VIDEO_EXTS: &[&str] = &["mkv", "mp4", "avi", "webm", "mov", "flv", "m4v", "wmv"];

/// Pick the best file index from a list of `(name, size)` pairs.
///
/// Priority:
/// 1. `file_index` if valid
/// 2. Exact filename match (case-insensitive contains)
/// 3. Season+episode pattern (`SxxExx` or `XxExx`)
/// 4. Largest video file
/// 5. 0 as fallback
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

    // 1. file_index hint
    if let Some(fi) = file_index {
        if fi >= 0 && (fi as usize) < files.len() {
            return fi as usize;
        }
    }

    // Restrict remaining comparisons to video files only
    let video_indices: Vec<usize> = files
        .iter()
        .enumerate()
        .filter(|(_, (name, _))| {
            let ext = std::path::Path::new(name.as_str())
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("")
                .to_lowercase();
            VIDEO_EXTS.contains(&ext.as_str())
        })
        .map(|(i, _)| i)
        .collect();

    // 2. Filename match (search all files, not just video, to stay faithful)
    if let Some(name) = filename {
        let name_lower = name.to_lowercase();
        if let Some(idx) = files
            .iter()
            .position(|(n, _)| n.to_lowercase().contains(&name_lower))
        {
            return idx;
        }
    }

    // 3. Season + episode pattern in name
    if let (Some(s), Some(e)) = (season, episode) {
        let patterns = [format!("s{:02}e{:02}", s, e), format!("{:01}x{:02}", s, e)];
        let candidate = video_indices.iter().find(|&&i| {
            let lower = files[i].0.to_lowercase();
            patterns.iter().any(|p| lower.contains(p))
        });
        if let Some(&idx) = candidate {
            return idx;
        }
        // Also try non-video files as fallback for season/episode matching
        if let Some(idx) = files.iter().position(|(n, _)| {
            let lower = n.to_lowercase();
            patterns.iter().any(|p| lower.contains(p))
        }) {
            return idx;
        }
    }

    // 4. Largest video file
    if let Some(&idx) = video_indices.iter().max_by_key(|&&i| files[i].1) {
        return idx;
    }

    0
}

// ─── Flatten AllDebrid nested file tree ───────────────────────────────────────

/// AllDebrid returns files as a nested tree where leaf nodes have `"l"` (link),
/// `"n"` (name), `"s"` (size) and non-leaf nodes have `"n"` and `"e"` (entries).
///
/// This function flattens the tree into a `Vec<(name, size, link)>`.
fn flatten_ad_files(node: &Value, out: &mut Vec<(String, i64, String)>) {
    match node {
        Value::Array(arr) => {
            for item in arr {
                flatten_ad_files(item, out);
            }
        }
        Value::Object(_) => {
            // Leaf: has a link field "l"
            if let Some(link) = node.get("l").and_then(|v| v.as_str()) {
                let name = node
                    .get("n")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let size = node.get("s").and_then(|v| v.as_i64()).unwrap_or(0);
                out.push((name, size, link.to_string()));
            } else if let Some(entries) = node.get("e") {
                // Non-leaf: recurse into entries
                flatten_ad_files(entries, out);
            }
        }
        _ => {}
    }
}

// ─── AllDebrid API operations ─────────────────────────────────────────────────

/// GET /magnet/status — returns the full data value.
async fn get_magnet_status(
    http: &reqwest::Client,
    token: &str,
    id: Option<i64>,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let mut params: Vec<(&str, String)> = Vec::new();
    if let Some(i) = id {
        params.push(("id", i.to_string()));
    }
    let body = ad_get(http, token, "/magnet/status", &params, user_ip, forward).await?;
    Ok(body)
}

/// DELETE (via GET) /magnet/delete?ids[]={id}
async fn delete_magnet(
    http: &reqwest::Client,
    token: &str,
    id: i64,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    let params = [("ids[]", id.to_string())];
    ad_get(http, token, "/magnet/delete", &params, user_ip, forward).await?;
    Ok(())
}

/// POST /magnet/upload with `magnets[]={magnet}`. Returns the magnet id.
async fn upload_magnet(
    http: &reqwest::Client,
    token: &str,
    magnet: &str,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<i64, ProviderError> {
    let fields = [("magnets[]", magnet.to_string())];
    let body = ad_post_form(http, token, "/magnet/upload", &fields, user_ip, forward).await?;

    // data.magnets can be a list or a dict (single-element shorthand)
    let magnets = body
        .get("data")
        .and_then(|d| d.get("magnets"))
        .ok_or_else(|| {
            ProviderError::api(
                "Missing data.magnets in upload response",
                "transfer_error.mp4",
            )
        })?;

    let first = match magnets {
        Value::Array(arr) => arr.first().ok_or_else(|| {
            ProviderError::api(
                "Empty magnets array in upload response",
                "transfer_error.mp4",
            )
        })?,
        Value::Object(_) => magnets,
        _ => {
            return Err(ProviderError::api(
                "Unexpected magnets shape in upload response",
                "transfer_error.mp4",
            ))
        }
    };

    first.get("id").and_then(|v| v.as_i64()).ok_or_else(|| {
        ProviderError::api("Missing id in magnet upload response", "transfer_error.mp4")
    })
}

/// GET /magnet/files?id[]={id} — returns flattened `(name, size, link)` triples.
async fn get_magnet_files(
    http: &reqwest::Client,
    token: &str,
    id: i64,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<(String, i64, String)>, ProviderError> {
    let params = [("id[]", id.to_string())];
    let body = ad_get(http, token, "/magnet/files", &params, user_ip, forward).await?;

    // data.magnets is an array; each element has "files" (the nested tree)
    let magnets = body
        .get("data")
        .and_then(|d| d.get("magnets"))
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            ProviderError::api(
                "Missing data.magnets in files response",
                "transfer_error.mp4",
            )
        })?;

    let mut files: Vec<(String, i64, String)> = Vec::new();
    for magnet_entry in magnets {
        if let Some(f) = magnet_entry.get("files") {
            flatten_ad_files(f, &mut files);
        }
    }
    Ok(files)
}

/// GET /link/unlock?link={link} — returns the direct streaming URL.
async fn unlock_link(
    http: &reqwest::Client,
    token: &str,
    link: &str,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let params = [("link", link.to_string())];
    let body = ad_get(http, token, "/link/unlock", &params, user_ip, forward).await?;

    body.get("data")
        .and_then(|d| d.get("link"))
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| ProviderError::api("Missing data.link in unlock response", "api_error.mp4"))
}

// ─── Wait for "Ready" status ──────────────────────────────────────────────────

/// Poll /magnet/status?id={id} until statusCode != processing (statusCode 4 = Ready).
///
/// AllDebrid statusCodes:
///   0 = In queue, 1 = Downloading, 2 = Compressing/Moving, 3 = Uploading,
///   4 = Ready, 5 = Upload fail, 6 = Internal error, 7 = Not downloaded (error)
async fn wait_for_ready(
    http: &reqwest::Client,
    token: &str,
    id: i64,
    max_retries: u32,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    for attempt in 0..max_retries {
        let body = get_magnet_status(http, token, Some(id), user_ip, forward).await?;

        let magnets = body
            .get("data")
            .and_then(|d| d.get("magnets"))
            .ok_or_else(|| {
                ProviderError::api(
                    "Missing data.magnets in status response",
                    "transfer_error.mp4",
                )
            })?;

        // Single id query returns an object, not an array
        let magnet = match magnets {
            Value::Array(arr) => arr.first().cloned().unwrap_or(Value::Null),
            other => other.clone(),
        };

        let status_code = magnet
            .get("statusCode")
            .and_then(|v| v.as_i64())
            .unwrap_or(-1);

        match status_code {
            4 => return Ok(()), // Ready
            7 => {
                // Error state — caller will handle delete
                return Err(ProviderError::api(
                    "AllDebrid torrent entered error state (statusCode 7)",
                    "torrent_not_downloaded.mp4",
                ));
            }
            5 | 6 => {
                return Err(ProviderError::api(
                    format!("AllDebrid torrent failed with statusCode {status_code}"),
                    "transfer_error.mp4",
                ));
            }
            _ => {
                // Still processing
                if attempt + 1 < max_retries {
                    tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
                }
            }
        }
    }

    Err(ProviderError::api(
        format!("AllDebrid torrent did not become Ready after {max_retries} attempts"),
        "torrent_not_downloaded.mp4",
    ))
}

// ─── Find torrent by hash in existing library ─────────────────────────────────

/// Scan /magnet/status (all torrents) and return the magnet entry whose hash
/// matches `info_hash` (case-insensitive).
async fn find_magnet_by_hash(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<Value>, ProviderError> {
    let body = get_magnet_status(http, token, None, user_ip, forward).await?;

    let magnets = match body.get("data").and_then(|d| d.get("magnets")) {
        Some(Value::Array(arr)) => arr.clone(),
        _ => return Ok(None),
    };

    let hash_lower = info_hash.to_lowercase();
    for m in magnets {
        if m.get("hash")
            .and_then(|v| v.as_str())
            .map(str::to_lowercase)
            .as_deref()
            == Some(&hash_lower)
        {
            return Ok(Some(m));
        }
    }
    Ok(None)
}

// ─── Public entry point ────────────────────────────────────────────────────────

/// Resolve a direct video URL from AllDebrid for the given torrent.
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
    const MAX_RETRIES: u32 = 5;

    // Build magnet URI
    let trackers: String = announce_list
        .iter()
        .map(|t| format!("&tr={}", urlencoding::encode(t)))
        .collect();
    let magnet = format!("magnet:?xt=urn:btih:{info_hash}{trackers}");

    // Check whether we already have this torrent
    let magnet_id: i64 = match find_magnet_by_hash(http, token, info_hash, user_ip, forward).await?
    {
        Some(existing) => {
            let status_code = existing
                .get("statusCode")
                .and_then(|v| v.as_i64())
                .unwrap_or(-1);
            let id = existing.get("id").and_then(|v| v.as_i64()).ok_or_else(|| {
                ProviderError::api("Missing id in existing magnet", "transfer_error.mp4")
            })?;

            if status_code == 7 {
                // Error state — delete and re-add
                delete_magnet(http, token, id, user_ip, forward).await.ok();
                upload_magnet(http, token, &magnet, user_ip, forward).await?
            } else if status_code == 4 {
                // Already ready — skip polling
                id
            } else {
                id
            }
        }
        None => upload_magnet(http, token, &magnet, user_ip, forward).await?,
    };

    // Wait for the torrent to be ready
    match wait_for_ready(http, token, magnet_id, MAX_RETRIES, user_ip, forward).await {
        Ok(()) => {}
        Err(e) => {
            // If it stuck in error, clean up and propagate
            let msg = e.to_string();
            if msg.contains("statusCode 7") || msg.contains("error state") {
                delete_magnet(http, token, magnet_id, user_ip, forward)
                    .await
                    .ok();
            }
            return Err(e);
        }
    }

    // Fetch and flatten files
    let files_raw = get_magnet_files(http, token, magnet_id, user_ip, forward).await?;

    if files_raw.is_empty() {
        return Err(ProviderError::api(
            "No files returned for AllDebrid torrent",
            "torrent_not_downloaded.mp4",
        ));
    }

    // Build (name, size) slice for selector
    let name_size: Vec<(String, i64)> = files_raw.iter().map(|(n, s, _)| (n.clone(), *s)).collect();

    let selected_idx = select_video_file(&name_size, filename, file_index, season, episode);

    let link = files_raw
        .get(selected_idx)
        .map(|(_, _, l)| l.as_str())
        .ok_or_else(|| {
            ProviderError::api(
                "Selected file index out of range in AllDebrid response",
                "torrent_not_downloaded.mp4",
            )
        })?;

    // Unlock the link to get the direct URL
    unlock_link(http, token, link, user_ip, forward).await
}

// ─── Delete by hash / Delete all torrents ─────────────────────────────────────

/// Delete the magnet matching `info_hash` from AllDebrid.
/// Returns `true` if found and deleted, `false` if not found.
pub async fn delete_torrent_by_hash(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
) -> Result<bool, ProviderError> {
    match find_magnet_by_hash(http, token, info_hash, None, None).await? {
        None => Ok(false),
        Some(magnet) => {
            if let Some(id) = magnet.get("id").and_then(|v| v.as_i64()) {
                delete_magnet(http, token, id, None, None).await.ok();
            }
            Ok(true)
        }
    }
}

/// Delete ALL magnets from the user's AllDebrid account (implements delete-all-watchlist).
pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    let body = get_magnet_status(http, token, None, None, None).await?;

    let magnets = match body.get("data").and_then(|d| d.get("magnets")) {
        Some(Value::Array(arr)) => arr.clone(),
        _ => return Ok(()),
    };

    // Collect all ids
    let ids: Vec<i64> = magnets
        .iter()
        .filter_map(|m| m.get("id").and_then(|v| v.as_i64()))
        .collect();

    if ids.is_empty() {
        return Ok(());
    }

    // AllDebrid allows bulk deletion via repeated ids[] params
    let url = format!("{BASE_URL}/magnet/delete");
    let query: Vec<(&str, String)> = std::iter::once(("agent", AGENT.to_string()))
        .chain(ids.iter().map(|id| ("ids[]", id.to_string())))
        .collect();

    http.get(&url)
        .bearer_auth(token)
        .query(&query)
        .send()
        .await?;

    Ok(())
}

// ─── Debrid cache check ───────────────────────────────────────────────────────

/// Check which hashes are in the user's AllDebrid account (status=ready).
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    use std::collections::HashSet;
    let url = format!("{BASE_URL}/magnet/status");
    let resp = match http
        .get(&url)
        .bearer_auth(token)
        .query(&[("agent", AGENT), ("status", "ready")])
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!("alldebrid magnet/status: {e}");
            return vec![];
        }
    };
    let body: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("alldebrid magnet/status json: {e}");
            return vec![];
        }
    };
    if body.get("status").and_then(|v| v.as_str()) != Some("success") {
        return vec![];
    }
    let hash_set: HashSet<String> = hashes.iter().map(|h| h.to_lowercase()).collect();
    let mut found = Vec::new();
    if let Some(magnets_val) = body.get("data").and_then(|d| d.get("magnets")) {
        let iter_magnets = |m: &serde_json::Value| {
            if let Some(h) = m.get("hash").and_then(|v| v.as_str()) {
                let lower = h.to_lowercase();
                if hash_set.contains(&lower) {
                    return Some(lower);
                }
            }
            None
        };
        if let Some(arr) = magnets_val.as_array() {
            found.extend(arr.iter().filter_map(iter_magnets));
        } else if let Some(obj) = magnets_val.as_object() {
            found.extend(obj.values().filter_map(iter_magnets));
        }
    }
    found
}
