/// Seedr v2 streaming provider.
///
/// API base: https://v2.seedr.cc/api/v0.1/p
/// Auth: Bearer token (Personal Access Token or OAuth access_token).
///
/// Token format accepted:
///   - PAT string directly
///   - Base64-encoded JSON: {"access_token": "...", ...}
use base64::{engine::general_purpose::STANDARD, Engine};
use reqwest::Client;
use serde_json::{json, Value};

use crate::providers::{
    torrents::transport::{encode_form_body, MediaFlowForward},
    ProviderError,
};

const BASE_URL: &str = "https://v2.seedr.cc/api/v0.1/p";
const MAX_RETRIES: u32 = 3;
const RETRY_SECS: u64 = 5;

static VIDEO_EXTS: &[&str] = &["mkv", "mp4", "avi", "webm", "mov", "flv", "m4v", "wmv"];

// ─── Token ────────────────────────────────────────────────────────────────────

fn resolve_token(raw: &str) -> String {
    if let Ok(decoded) = STANDARD.decode(raw.trim()) {
        if let Ok(s) = String::from_utf8(decoded) {
            if let Ok(v) = serde_json::from_str::<Value>(&s) {
                if let Some(t) = v["access_token"].as_str() {
                    return t.to_string();
                }
            }
        }
    }
    raw.trim().to_string()
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

async fn api_get(
    http: &Client,
    token: &str,
    path: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = if let Some(fwd) = forward {
        fwd.get(http, &url, token).await?
    } else {
        http.get(&url).bearer_auth(token).send().await?
    };
    handle_response(resp).await
}

async fn api_post(
    http: &Client,
    token: &str,
    path: &str,
    body: &Value,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = if let Some(fwd) = forward {
        fwd.post_json(http, &url, token, body.to_string()).await?
    } else {
        http.post(&url).bearer_auth(token).json(body).send().await?
    };
    handle_response(resp).await
}

async fn api_delete(
    http: &Client,
    token: &str,
    path: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = if let Some(fwd) = forward {
        fwd.delete(http, &url, token).await?
    } else {
        http.delete(&url).bearer_auth(token).send().await?
    };
    let status = resp.status();
    if status == reqwest::StatusCode::UNAUTHORIZED {
        return Err(ProviderError::api(
            "Seedr token is expired or invalid. Please reconnect your Seedr account.",
            "invalid_token.mp4",
        ));
    }
    Ok(())
}

async fn handle_response(resp: reqwest::Response) -> Result<Value, ProviderError> {
    let status = resp.status();
    match status.as_u16() {
        401 => {
            return Err(ProviderError::api(
                "Seedr token is expired or invalid. Please reconnect your Seedr account.",
                "invalid_token.mp4",
            ))
        }
        402 | 403 => {
            return Err(ProviderError::api(
                "Seedr premium plan required for this operation.",
                "debrid_service_down_error.mp4",
            ))
        }
        429 => {
            return Err(ProviderError::api(
                "Seedr rate limit exceeded. Please try again later.",
                "api_error.mp4",
            ))
        }
        500..=599 => {
            return Err(ProviderError::api(
                "Seedr service is temporarily unavailable.",
                "debrid_service_down_error.mp4",
            ))
        }
        _ => {}
    }
    let body: Value = resp.json().await.unwrap_or_default();
    if !status.is_success() {
        let msg = body["error_description"]
            .as_str()
            .or_else(|| body["error"].as_str())
            .unwrap_or("Seedr API error");
        return Err(ProviderError::api(msg.to_string(), "api_error.mp4"));
    }
    Ok(body)
}

// ─── Magnet ───────────────────────────────────────────────────────────────────

fn build_magnet(info_hash: &str, announce_list: &[String]) -> String {
    let trackers = announce_list
        .iter()
        .map(|t| format!("tr={}", urlencoding::encode(t)))
        .collect::<Vec<_>>()
        .join("&");
    if trackers.is_empty() {
        format!("magnet:?xt=urn:btih:{info_hash}")
    } else {
        format!("magnet:?xt=urn:btih:{info_hash}&{trackers}")
    }
}

fn extract_info_hash_from_magnet(magnet: &str) -> Option<String> {
    let lower = magnet.to_lowercase();
    let prefix = "urn:btih:";
    let pos = lower.find(prefix)?;
    let rest = &magnet[pos + prefix.len()..];
    let hash: String = rest
        .chars()
        .take_while(|c| c.is_ascii_alphanumeric())
        .collect();
    if hash.len() >= 32 {
        Some(hash.to_lowercase())
    } else {
        None
    }
}

// ─── Task helpers ─────────────────────────────────────────────────────────────

async fn list_tasks(
    http: &Client,
    token: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<Value>, ProviderError> {
    let resp = api_get(http, token, "/tasks", forward).await?;
    Ok(resp["tasks"].as_array().cloned().unwrap_or_default())
}

fn task_info_hash(task: &Value) -> Option<String> {
    // v2: hash is nested inside torrent_payload
    if let Some(h) = task["torrent_payload"]["hash"]
        .as_str()
        .filter(|s| !s.is_empty())
    {
        return Some(h.to_lowercase());
    }
    // fallback: direct hash field (older API shape)
    if let Some(h) = task["hash"].as_str().filter(|s| !s.is_empty()) {
        return Some(h.to_lowercase());
    }
    // Extract from magnet URL in various possible locations
    let magnet = task["url"]
        .as_str()
        .or_else(|| task["params"]["url"].as_str())
        .or_else(|| task["torrent_url"].as_str());
    if let Some(m) = magnet {
        return extract_info_hash_from_magnet(m);
    }
    None
}

fn task_is_complete(task: &Value) -> bool {
    // v2 uses "state"; older API used "status"
    let s = task["state"]
        .as_str()
        .or_else(|| task["status"].as_str())
        .unwrap_or("");
    if matches!(s, "finished" | "seeding" | "stopped" | "idle") {
        return true;
    }
    // v2 progress is 0-100 integer; older API used 0.0-1.0 float
    if let Some(p) = task["progress"].as_i64() {
        if p >= 100 {
            return true;
        }
    }
    if let Some(p) = task["progress"].as_f64() {
        if p >= 1.0 {
            return true;
        }
    }
    false
}

fn task_is_downloading(task: &Value) -> bool {
    let s = task["state"]
        .as_str()
        .or_else(|| task["status"].as_str())
        .unwrap_or("");
    matches!(
        s,
        "downloading" | "queued" | "active" | "pending" | "waiting"
    )
}

// ─── File collection ──────────────────────────────────────────────────────────

// Returns Vec<(name, size, file_id)> collecting all files recursively
fn collect_files(v: &Value, out: &mut Vec<(String, i64, i64)>) {
    if let Some(files) = v["files"].as_array() {
        for f in files {
            let name = f["name"].as_str().unwrap_or("").to_string();
            let size = f["size"].as_i64().unwrap_or(0);
            let id = f["id"].as_i64().unwrap_or(0);
            if id > 0 && !name.is_empty() {
                out.push((name, size, id));
            }
        }
    }
    // Recurse into nested folders if present
    if let Some(folders) = v["folders"].as_array() {
        for folder in folders {
            collect_files(folder, out);
        }
    }
}

async fn folder_files_recursive(
    http: &Client,
    token: &str,
    folder_id: i64,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<(String, i64, i64)>, ProviderError> {
    let resp = api_get(
        http,
        token,
        &format!("/fs/folder/{folder_id}/contents"),
        forward,
    )
    .await?;
    let mut files = Vec::new();
    collect_files(&resp, &mut files);
    // Recurse into subfolders
    if let Some(subfolders) = resp["folders"].as_array() {
        for sf in subfolders {
            if let Some(sfid) = sf["id"].as_i64() {
                let sub = Box::pin(folder_files_recursive(http, token, sfid, forward)).await?;
                files.extend(sub);
            }
        }
    }
    Ok(files)
}

// ─── Video file selection ─────────────────────────────────────────────────────

fn is_video(name: &str) -> bool {
    let lower = name.to_lowercase();
    VIDEO_EXTS.iter().any(|e| lower.ends_with(&format!(".{e}")))
}

fn select_video<'a>(
    files: &'a [(String, i64, i64)],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<&'a (String, i64, i64)> {
    let videos: Vec<_> = files.iter().filter(|(n, _, _)| is_video(n)).collect();
    if videos.is_empty() {
        return None;
    }

    // 1. Explicit index
    if let Some(idx) = file_index {
        if let Some(f) = videos.get(idx as usize) {
            return Some(f);
        }
    }

    // 2. Filename substring match
    if let Some(fname) = filename {
        let fname_lower = fname.to_lowercase();
        if let Some(f) = videos
            .iter()
            .find(|(n, _, _)| n.to_lowercase().contains(&fname_lower))
        {
            return Some(f);
        }
    }

    // 3. Season/episode pattern
    if let (Some(s), Some(e)) = (season, episode) {
        let patterns = [
            format!("s{s:02}e{e:02}"),
            format!("{s}x{e:02}"),
            format!("{s:02}x{e:02}"),
        ];
        for f in &videos {
            let lower = f.0.to_lowercase();
            if patterns.iter().any(|p| lower.contains(p.as_str())) {
                return Some(f);
            }
        }
    }

    // 4. Largest video
    videos.into_iter().max_by_key(|(_, sz, _)| sz)
}

// ─── Download URL ─────────────────────────────────────────────────────────────

async fn file_url(
    http: &Client,
    token: &str,
    file_id: i64,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    // Try video presentation first (streaming URL)
    if let Ok(resp) = api_get(
        http,
        token,
        &format!("/presentations/file/{file_id}/video"),
        forward,
    )
    .await
    {
        if let Some(url) = resp["url"]
            .as_str()
            .or_else(|| resp["stream_url"].as_str())
            .or_else(|| resp["link"]["url"].as_str())
        {
            return Ok(url.to_string());
        }
    }

    // Fall back to direct download URL
    let resp = api_get(
        http,
        token,
        &format!("/download/file/{file_id}/url"),
        forward,
    )
    .await?;
    resp["url"]
        .as_str()
        .or_else(|| resp["download_url"].as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            ProviderError::api(
                "Seedr returned no download URL for this file.",
                "api_error.mp4",
            )
        })
}

// ─── Internal resolution ──────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
async fn resolve_from_folder(
    http: &Client,
    token: &str,
    folder_id: i64,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let files = folder_files_recursive(http, token, folder_id, forward).await?;
    let sel = select_video(&files, filename, file_index, season, episode).ok_or_else(|| {
        ProviderError::api(
            "No matching video file found in Seedr folder.",
            "no_matching_file.mp4",
        )
    })?;
    file_url(http, token, sel.2, forward).await
}

// ─── Folder-per-hash helpers ──────────────────────────────────────────────────

enum TorrentStatus {
    NotFound,
    Downloading,
    /// ID of the content sub-folder placed inside the hash-named folder by Seedr.
    Completed(i64),
}

/// Find the root-level folder whose `path` matches `hash`.
/// Returns `(hash_folder_id, Option<content_subfolder_id>)` — the sub-folder is
/// populated once Seedr finishes placing the downloaded content there.
async fn find_hash_folder(
    http: &Client,
    token: &str,
    hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<(i64, Option<i64>)>, ProviderError> {
    let root = api_get(http, token, "/fs/root/contents", forward).await?;
    let Some(folders) = root["folders"].as_array() else {
        return Ok(None);
    };
    for folder in folders {
        // Root folders use the `path` field as their display name (no separate `name` field).
        let path = folder["path"].as_str().unwrap_or("").to_lowercase();
        if path != hash {
            continue;
        }
        let Some(folder_id) = folder["id"].as_i64() else {
            continue;
        };
        let contents = api_get(
            http,
            token,
            &format!("/fs/folder/{folder_id}/contents"),
            forward,
        )
        .await?;
        let sub_id = contents["folders"]
            .as_array()
            .and_then(|sf| sf.first())
            .and_then(|sf| sf["id"].as_i64());
        return Ok(Some((folder_id, sub_id)));
    }
    Ok(None)
}

/// Determine the current status of the torrent identified by `hash`.
///
/// - Active tasks  → `Downloading`
/// - Hash folder with a content sub-folder → `Completed(sub_folder_id)`
/// - Hash folder empty + no task → orphaned, deletes folder → `NotFound`
/// - Nothing found → `NotFound`
async fn check_status(
    http: &Client,
    token: &str,
    hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<TorrentStatus, ProviderError> {
    // The v2 /tasks endpoint retains completed tasks, so we must check state explicitly.
    let tasks = list_tasks(http, token, forward).await.unwrap_or_default();

    let hash_task = tasks
        .iter()
        .find(|t| task_info_hash(t).as_deref() == Some(hash));

    if let Some(task) = hash_task {
        if task_is_downloading(task) {
            return Ok(TorrentStatus::Downloading);
        }
        if task_is_complete(task) {
            if let Some(content_id) = task["folder_created_id"].as_i64() {
                return Ok(TorrentStatus::Completed(content_id));
            }
        }
    }

    // Scan root filesystem for a hash-named folder.
    match find_hash_folder(http, token, hash, forward).await? {
        Some((_, Some(content_id))) => Ok(TorrentStatus::Completed(content_id)),
        Some((folder_id, None)) => {
            if hash_task.is_none() {
                // Orphaned: folder exists but no content and no active task — clean up.
                tracing::info!("Seedr: removing orphaned empty folder for hash {hash}");
                api_delete(http, token, &format!("/fs/folder/{folder_id}"), forward)
                    .await
                    .ok();
                Ok(TorrentStatus::NotFound)
            } else {
                Ok(TorrentStatus::Downloading)
            }
        }
        None => Ok(TorrentStatus::NotFound),
    }
}

/// Create (or reuse) a root-level folder named with `hash`. Returns its folder ID.
///
/// Uses `POST /fs/folder` with form-encoded `parent_id=0&name=<hash>`.
/// The API returns `{"success": true, "id": "<string_id>", "path": "<hash>"}`.
/// On conflict (folder already exists) falls back to `find_hash_folder`.
async fn ensure_hash_folder(
    http: &Client,
    token: &str,
    hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<i64, ProviderError> {
    let url = format!("{BASE_URL}/fs/folder");
    let resp = if let Some(fwd) = forward {
        let body_str = encode_form_body(&[("parent_id", "0"), ("name", hash)]);
        fwd.post_form(http, &url, token, body_str).await?
    } else {
        http.post(&url)
            .bearer_auth(token)
            .form(&[("parent_id", "0"), ("name", hash)])
            .send()
            .await?
    };

    let status = resp.status();
    if status == reqwest::StatusCode::UNAUTHORIZED {
        return Err(ProviderError::api(
            "Seedr token is expired or invalid. Please reconnect your Seedr account.",
            "invalid_token.mp4",
        ));
    }

    if status.is_success() {
        let body: serde_json::Value = resp.json().await.unwrap_or_default();
        // The API returns `id` as a string (e.g. "1419838690").
        let id = body["id"]
            .as_str()
            .and_then(|s| s.parse::<i64>().ok())
            .or_else(|| body["id"].as_i64());
        if let Some(id) = id {
            return Ok(id);
        }
    }

    // Folder may already exist (409) or ID was missing — look it up.
    match find_hash_folder(http, token, hash, forward).await? {
        Some((id, _)) => Ok(id),
        None => Err(ProviderError::api(
            "Failed to create or locate Seedr folder for this torrent.",
            "api_error.mp4",
        )),
    }
}

/// Submit a magnet link to Seedr, directing the download into `folder_id`.
///
/// Uses `POST /tasks` with `{"torrent_magnet": magnet, "folder_id": folder_id}`.
/// Returns the new task ID.
async fn add_torrent_to_folder(
    http: &Client,
    token: &str,
    magnet: &str,
    folder_id: i64,
    forward: Option<&MediaFlowForward>,
) -> Result<i64, ProviderError> {
    let add_resp = api_post(
        http,
        token,
        "/tasks",
        &json!({"torrent_magnet": magnet, "folder_id": folder_id}),
        forward,
    )
    .await
    .map_err(|e| {
        ProviderError::api(
            format!("Failed to add torrent to Seedr: {e}"),
            "transfer_error.mp4",
        )
    })?;

    // The API uses `reason_phrase` for soft errors (added to wishlist) and `error` for hard errors.
    let error_code = add_resp["reason_phrase"]
        .as_str()
        .or_else(|| add_resp["error"].as_str())
        .unwrap_or("");
    match error_code {
        "not_enough_space" | "not_enough_space_added_to_wishlist" => {
            return Err(ProviderError::api(
                "Not enough storage space in your Seedr account.",
                "not_enough_space.mp4",
            ))
        }
        "queue_full" | "queue_full_added_to_wishlist" => {
            return Err(ProviderError::api(
                "Seedr download queue is full. Please wait for current downloads to finish.",
                "queue_full.mp4",
            ))
        }
        "" => {}
        other => {
            return Err(ProviderError::api(
                format!("Seedr rejected the torrent: {other}"),
                "transfer_error.mp4",
            ))
        }
    }

    add_resp["user_torrent_id"]
        .as_i64()
        .or_else(|| add_resp["id"].as_i64())
        .or_else(|| add_resp["task"]["id"].as_i64())
        .ok_or_else(|| {
            ProviderError::api(
                "Seedr did not return a task ID after adding torrent.",
                "transfer_error.mp4",
            )
        })
}

// ─── Storage management ───────────────────────────────────────────────────────

/// Ensure at least `required_bytes` are free in the Seedr account.
/// Fetches root contents (which includes space_max / space_used) in one call,
/// then deletes hash-named folders from largest to smallest until enough space is freed.
/// `required_bytes` = 0 uses a 1 GiB safety minimum.
/// Non-fatal: on any API failure, logs a warning and returns without blocking the download.
async fn ensure_enough_space(
    http: &Client,
    token: &str,
    required_bytes: i64,
    forward: Option<&MediaFlowForward>,
) {
    let minimum = if required_bytes > 0 {
        required_bytes
    } else {
        1_073_741_824 // 1 GiB
    };

    let root = match api_get(http, token, "/fs/root/contents", forward).await {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!("Seedr: could not fetch root contents for space check: {e}");
            return;
        }
    };

    // space_max and space_used are top-level fields in the root contents response.
    let space_max = root["space_max"].as_i64().unwrap_or(0);
    let space_used = root["space_used"].as_i64().unwrap_or(0);
    let free = (space_max - space_used).max(0);

    if space_max == 0 {
        tracing::warn!(
            "Seedr: could not read storage quota from root contents; skipping space check"
        );
        return;
    }

    if free >= minimum {
        return;
    }

    tracing::info!("Seedr: only {free} bytes free, need {minimum} — cleaning up old downloads");

    // Delete ALL root folders sorted largest-first until we have enough space.
    // Root only contains user downloads so it's safe to clear anything here.
    let mut candidates: Vec<(i64, i64)> = root["folders"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|f| {
            let id = f["id"].as_i64()?;
            let size = f["size"].as_i64().unwrap_or(0);
            Some((size, id))
        })
        .collect();
    candidates.sort_by_key(|b| std::cmp::Reverse(b.0));

    let mut freed = 0i64;
    for (size, id) in candidates {
        if free + freed >= minimum {
            break;
        }
        tracing::info!("Seedr: deleting folder {id} ({size} bytes) to free space");
        api_delete(http, token, &format!("/fs/folder/{id}"), forward)
            .await
            .ok();
        freed += size;
    }
}

// ─── Public entry point ───────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
pub async fn get_video_url(
    http: &Client,
    token: &str,
    info_hash: &str,
    announce_list: &[String],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    size_bytes: Option<i64>,
    _user_ip: Option<&str>,
    forward: Option<&crate::providers::torrents::transport::MediaFlowForward>,
) -> Result<String, ProviderError> {
    let token = resolve_token(token);
    let hash = info_hash.to_lowercase();
    let magnet = build_magnet(&hash, announce_list);

    let content_folder_id = match check_status(http, &token, &hash, forward).await? {
        TorrentStatus::Downloading => {
            return Err(ProviderError::api(
                "Torrent is still downloading in Seedr. Please try again later.",
                "torrent_not_downloaded.mp4",
            ));
        }
        TorrentStatus::Completed(id) => id,
        TorrentStatus::NotFound => {
            // Ensure there is enough space, freeing old downloads if necessary.
            ensure_enough_space(http, &token, size_bytes.unwrap_or(0), forward).await;

            // Create a named folder for tracking, then submit the torrent into it.
            let folder_id = ensure_hash_folder(http, &token, &hash, forward).await?;
            let task_id = add_torrent_to_folder(http, &token, &magnet, folder_id, forward).await?;

            // Poll until the content sub-folder appears inside the hash folder.
            let mut content_id: Option<i64> = None;
            for _ in 0..MAX_RETRIES {
                tokio::time::sleep(tokio::time::Duration::from_secs(RETRY_SECS)).await;

                // Check task for fatal errors.
                if let Ok(full_resp) =
                    api_get(http, &token, &format!("/tasks/{task_id}"), forward).await
                {
                    let task_resp = if full_resp["task"].is_object() {
                        full_resp["task"].clone()
                    } else {
                        full_resp
                    };
                    let state = task_resp["state"]
                        .as_str()
                        .or_else(|| task_resp["status"].as_str())
                        .unwrap_or("");
                    if matches!(state, "error" | "failed" | "dead") {
                        return Err(ProviderError::api(
                            "Seedr failed to download this torrent.",
                            "torrent_error.mp4",
                        ));
                    }
                    // Use folder_created_id directly when task is complete.
                    if task_is_complete(&task_resp) {
                        if let Some(fcid) = task_resp["folder_created_id"].as_i64() {
                            content_id = Some(fcid);
                            break;
                        }
                    }
                }

                // Fallback: check whether the content sub-folder has appeared in the hash folder.
                if let Ok(Some((_, Some(id)))) =
                    find_hash_folder(http, &token, &hash, forward).await
                {
                    content_id = Some(id);
                    break;
                }
            }

            content_id.ok_or_else(|| {
                ProviderError::api(
                    "Torrent is still downloading in Seedr. Please try again in a few minutes.",
                    "torrent_not_downloaded.mp4",
                )
            })?
        }
    };

    resolve_from_folder(
        http,
        &token,
        content_folder_id,
        filename,
        file_index,
        season,
        episode,
        forward,
    )
    .await
}

// ─── Cache check ──────────────────────────────────────────────────────────────

/// Check which of the given info hashes are already downloaded in the user's Seedr account.
///
/// A hash is considered cached when its hash-named root folder exists and has a non-empty
/// content subfolder (size > 0), OR when a completed task for that hash has a `folder_created_id`.
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    use std::collections::HashSet;

    let bearer = resolve_token(token);
    let hash_set: HashSet<String> = hashes.iter().map(|h| h.to_lowercase()).collect();
    let mut cached: Vec<String> = Vec::new();

    // Root contents gives us both storage info and all folders in one call.
    let root = match api_get(http, &bearer, "/fs/root/contents", None).await {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!("Seedr check_cached: failed to fetch root contents: {e}");
            return cached;
        }
    };

    // Hash-named folder with size > 0 means content is already inside it.
    let folders = root["folders"].as_array().cloned().unwrap_or_default();
    let mut found: HashSet<String> = HashSet::new();
    for folder in &folders {
        let path = folder["path"].as_str().unwrap_or("").to_lowercase();
        if !hash_set.contains(&path) {
            continue;
        }
        let size = folder["size"].as_i64().unwrap_or(0);
        if size > 0 {
            cached.push(path.clone());
            found.insert(path);
        }
    }

    // For remaining hashes, check completed tasks with a folder_created_id.
    let remaining: Vec<String> = hash_set
        .iter()
        .filter(|h| !found.contains(*h))
        .cloned()
        .collect();
    if !remaining.is_empty() {
        if let Ok(tasks_resp) = api_get(http, &bearer, "/tasks", None).await {
            for task in tasks_resp["tasks"].as_array().cloned().unwrap_or_default() {
                if !task_is_complete(&task) {
                    continue;
                }
                if task["folder_created_id"].as_i64().is_none() {
                    continue;
                }
                if let Some(hash) = task_info_hash(&task) {
                    if remaining.contains(&hash) {
                        cached.push(hash);
                    }
                }
            }
        }
    }

    cached
}

/// Delete ALL active tasks and hash-named folders from the Seedr account.
pub async fn delete_all_torrents(http: &Client, token: &str) -> Result<(), ProviderError> {
    let bearer = resolve_token(token);

    let tasks = list_tasks(http, &bearer, None).await.unwrap_or_default();
    for task in &tasks {
        if let Some(id) = task["id"].as_i64() {
            api_delete(http, &bearer, &format!("/tasks/{id}"), None)
                .await
                .ok();
        }
    }

    // Delete root folders whose path looks like an info hash (40-char SHA-1 or 32-char MD5).
    if let Ok(root) = api_get(http, &bearer, "/fs/root/contents", None).await {
        if let Some(folders) = root["folders"].as_array() {
            for folder in folders {
                let path = folder["path"].as_str().unwrap_or("").to_lowercase();
                if path.len() == 40 || path.len() == 32 {
                    if let Some(id) = folder["id"].as_i64() {
                        api_delete(http, &bearer, &format!("/fs/folder/{id}"), None)
                            .await
                            .ok();
                    }
                }
            }
        }
    }

    Ok(())
}

/// Delete the active task and hash-named folder for `info_hash`.
/// Returns `true` if anything was deleted.
pub async fn delete_torrent_by_hash(
    http: &Client,
    token: &str,
    info_hash: &str,
) -> Result<bool, ProviderError> {
    let bearer = resolve_token(token);
    let hash_lower = info_hash.to_lowercase();
    let mut deleted = false;

    let tasks = list_tasks(http, &bearer, None).await.unwrap_or_default();
    for task in &tasks {
        if task_info_hash(task).as_deref() == Some(hash_lower.as_str()) {
            if let Some(id) = task["id"].as_i64() {
                api_delete(http, &bearer, &format!("/tasks/{id}"), None).await?;
                deleted = true;
            }
        }
    }

    if let Some((folder_id, _)) = find_hash_folder(http, &bearer, &hash_lower, None).await? {
        api_delete(http, &bearer, &format!("/fs/folder/{folder_id}"), None).await?;
        deleted = true;
    }

    Ok(deleted)
}
