/// TorBox streaming provider.
///
/// Token format: raw API token used directly as Bearer.
use serde_json::Value;

use crate::providers::{
    file_selection::select_debrid_file_index,
    response_json,
    torrents::transport::{encode_form_body, MediaFlowForward},
    ProviderError,
};

const BASE_URL: &str = "https://api.torbox.app/v1/api";

// ─── Video file selection helper ──────────────────────────────────────────────

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

// ─── Error mapping ────────────────────────────────────────────────────────────

fn map_torbox_error(error_code: &str) -> Option<(&'static str, &'static str)> {
    Some(match error_code {
        "BAD_TOKEN" | "AUTH_ERROR" => ("Invalid Torbox token", "invalid_token.mp4"),
        "DOWNLOAD_TOO_LARGE" => ("Download size too large", "not_enough_space.mp4"),
        "ACTIVE_LIMIT" | "MONTHLY_LIMIT" => ("Download limit exceeded", "daily_download_limit.mp4"),
        "PLAN_RESTRICTED_FEATURE" => ("Need premium TorBox account", "need_premium.mp4"),
        // Transient TorBox-side errors — service will recover on retry.
        "DATABASE_ERROR" | "DOWNSTREAM_ERROR" | "REQUEST_FAILED" => (
            "TorBox service error, please retry",
            "debrid_service_down_error.mp4",
        ),
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
    if body.get("success").and_then(|v| v.as_bool()) == Some(false) {
        let detail = body
            .get("detail")
            .and_then(|v| v.as_str())
            .unwrap_or("TorBox request failed");
        return Err(ProviderError::api(
            format!("TorBox: {detail}"),
            "transfer_error.mp4",
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
    let body: Value = response_json(resp, "tb_get").await?;
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
    let body: Value = response_json(resp, "tb_post_form").await?;
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
    let body: Value = response_json(resp, "tb_post_json").await?;
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

async fn create_torrent_file(
    http: &reqwest::Client,
    token: &str,
    torrent_bytes: &[u8],
    torrent_name: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}/torrents/createtorrent");
    let filename = torrent_name
        .filter(|n| !n.is_empty())
        .map(|n| {
            if n.ends_with(".torrent") {
                n.to_string()
            } else {
                format!("{n}.torrent")
            }
        })
        .unwrap_or_else(|| "torrent.torrent".to_string());

    let resp = if let Some(fwd) = forward {
        let boundary = "mediafusion_boundary_torrent_upload";
        let mut body: Vec<u8> = Vec::new();
        body.extend_from_slice(
            format!(
                "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/x-bittorrent\r\n\r\n"
            )
            .as_bytes(),
        );
        body.extend_from_slice(torrent_bytes);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());

        let content_type = format!("multipart/form-data; boundary={boundary}");
        fwd.post_raw(http, &url, token, &content_type, body).await?
    } else {
        let part = reqwest::multipart::Part::bytes(torrent_bytes.to_vec())
            .file_name(filename)
            .mime_str("application/x-bittorrent")
            .map_err(|e| ProviderError::Other(format!("TorBox: mime error: {e}")))?;
        let form = reqwest::multipart::Form::new().part("file", part);
        http.post(&url)
            .bearer_auth(token)
            .multipart(form)
            .send()
            .await?
    };

    let body: Value = response_json(resp, "torbox create_torrent_file").await?;
    check_torbox_error(&body)?;
    Ok(body)
}

async fn submit_torrent(
    http: &reqwest::Client,
    token: &str,
    magnet: &str,
    torrent_file: Option<&[u8]>,
    torrent_name: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    // Prefer magnet (fast). Fall back to .torrent upload if magnet fails.
    let magnet_resp = create_torrent(http, token, magnet, forward).await;
    match magnet_resp {
        Ok(resp) => return Ok(resp),
        Err(e) if torrent_file.filter(|b| !b.is_empty()).is_none() => return Err(e),
        Err(_) => {}
    }
    let bytes = torrent_file.filter(|b| !b.is_empty()).ok_or_else(|| {
        ProviderError::api("TorBox rejected the magnet link", "transfer_error.mp4")
    })?;
    create_torrent_file(http, token, bytes, torrent_name, forward).await
}

/// Build a TorBox `requestdl` URL with `redirect=true` so the player follows
/// straight to the CDN without an extra server-side JSON round trip.
fn request_download_link(
    token: &str,
    torrent_id: i64,
    file_id: i64,
    user_ip: Option<&str>,
) -> String {
    let mut url = format!(
        "{BASE_URL}/torrents/requestdl?token={}&torrent_id={torrent_id}&file_id={file_id}&redirect=true",
        urlencoding::encode(token)
    );
    if let Some(ip) = user_ip {
        url.push_str(&format!("&user_ip={}", urlencoding::encode(ip)));
    }
    url
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

fn build_download_link_from_torrent(
    token: &str,
    torrent: &Value,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    user_ip: Option<&str>,
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

    let release_name = torrent.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let idx = select_video_file(
        &name_size,
        release_name,
        filename,
        file_index,
        season,
        episode,
    );
    let file_id = raw_files[idx].0;

    Ok(request_download_link(token, torrent_id, file_id, user_ip))
}

async fn ready_playback_from_mylist(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    user_ip: Option<&str>,
) -> Result<String, ProviderError> {
    let resolved_user_ip = resolve_user_ip(http, user_ip, forward).await?;
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
                token,
                &torrent,
                filename,
                file_index,
                season,
                episode,
                resolved_user_ip.as_deref(),
            );
        }
    }
    Err(ProviderError::api(
        "Torrent is queued on TorBox but not yet downloaded",
        "torrent_not_downloaded.mp4",
    ))
}

async fn resolve_user_ip(
    http: &reqwest::Client,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<String>, ProviderError> {
    match user_ip {
        Some("{mediaflow_ip}") => match forward {
            Some(fwd) => fwd.get_public_ip(http).await.map(Some),
            None => Ok(None),
        },
        Some(ip) if !ip.is_empty() => Ok(Some(ip.to_string())),
        _ => Ok(None),
    }
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
    torrent_file: Option<&[u8]>,
    torrent_name: Option<&str>,
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
            let resolved_user_ip = resolve_user_ip(http, user_ip, forward).await?;
            return build_download_link_from_torrent(
                token,
                &torrent,
                filename,
                file_index,
                season,
                episode,
                resolved_user_ip.as_deref(),
            );
        }
        return Err(ProviderError::api(
            "Torrent is downloading on TorBox",
            "torrent_downloading.mp4",
        ));
    }

    // Check queued list before creating
    let queued = get_queued(http, token, forward)
        .await
        .unwrap_or(Value::Null);
    if is_torrent_queued(&queued, info_hash) {
        return Err(ProviderError::api(
            "Torrent is downloading on TorBox",
            "torrent_downloading.mp4",
        ));
    }

    // Add the torrent; DIFF_ISSUE means TorBox already has it (caching race with
    // our earlier mylist/queued check) — re-check mylist once.
    let create_resp =
        match submit_torrent(http, token, &magnet, torrent_file, torrent_name, forward).await {
            Ok(r) => r,
            Err(ProviderError::Api { ref message, .. }) if message.contains("DIFF_ISSUE") => {
                return ready_playback_from_mylist(
                    http, token, info_hash, forward, filename, file_index, season, episode, user_ip,
                )
                .await;
            }
            Err(e) => return Err(e),
        };

    let detail = create_resp
        .get("detail")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    if detail.contains("Found Cached") {
        return ready_playback_from_mylist(
            http, token, info_hash, forward, filename, file_index, season, episode, user_ip,
        )
        .await;
    }

    Err(ProviderError::api(
        "Torrent added to TorBox but not yet downloaded",
        "torrent_not_downloaded.mp4",
    ))
}

/// Delete the torrent matching `info_hash` from TorBox.
/// Returns `true` if found and deleted, `false` if not found.
/// Return all downloaded torrents with their files, ready for the missing-import flow.
pub async fn list_downloaded_torrents(
    http: &reqwest::Client,
    token: &str,
) -> Result<Vec<crate::providers::torrents::realdebrid::DownloadedTorrent>, ProviderError> {
    let list = get_mylist(http, token, None).await?;
    let arr = list
        .get("data")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    Ok(arr
        .into_iter()
        .filter_map(|t| {
            let hash = t.get("hash")?.as_str()?.to_lowercase();
            let id = t
                .get("id")
                .and_then(|v| v.as_i64())
                .map(|v| v.to_string())
                .unwrap_or_default();
            let name = t
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or(&hash)
                .to_string();
            let size = t.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
            let raw = t.clone();
            Some(crate::providers::torrents::realdebrid::DownloadedTorrent {
                id,
                info_hash: hash,
                name,
                size,
                raw,
            })
        })
        .collect())
}

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

fn collect_cached_hashes(data: &Value, cached: &mut Vec<String>) {
    match data {
        Value::Object(obj) => {
            for hash in obj.keys() {
                cached.push(hash.clone());
            }
        }
        Value::Array(arr) => {
            for v in arr {
                if let Some(h) = v.get("hash").and_then(|v| v.as_str()) {
                    cached.push(h.to_string());
                } else if let Some(h) = v.as_str() {
                    cached.push(h.to_string());
                }
            }
        }
        _ => {}
    }
}

/// Check which hashes are instantly cached on TorBox.
///
/// Uses the POST batch endpoint (`{ hashes: [...] }`) so large stream lists are
/// not limited by URL length. `format=object` — only cached hashes appear as keys.
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    if hashes.is_empty() {
        return Vec::new();
    }

    // POST accepts far more hashes per request than GET query params.
    const CHUNK: usize = 500;
    let mut cached = Vec::new();
    let url = format!("{BASE_URL}/torrents/checkcached?format=object");

    for chunk in hashes.chunks(CHUNK) {
        let payload = serde_json::json!({ "hashes": chunk });
        let resp = match http
            .post(&url)
            .bearer_auth(token)
            .json(&payload)
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                // Best-effort: transport failure just skips the chunk; treat hashes as uncached.
                tracing::debug!(
                    hashes = chunk.len(),
                    error_kind = crate::util::http::transport_error_kind(&e),
                    "torbox checkcached: {e}"
                );
                continue;
            }
        };
        let body: Value = match response_json(resp, "torbox checkcached").await {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(data) = body.get("data") {
            collect_cached_hashes(data, &mut cached);
        }
    }
    cached
}
