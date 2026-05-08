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

use super::ProviderError;

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

async fn api_get(http: &Client, token: &str, path: &str) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = http.get(&url).bearer_auth(token).send().await?;
    handle_response(resp).await
}

async fn api_post(http: &Client, token: &str, path: &str, body: &Value) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = http.post(&url).bearer_auth(token).json(body).send().await?;
    handle_response(resp).await
}

async fn api_delete(http: &Client, token: &str, path: &str) -> Result<(), ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = http.delete(&url).bearer_auth(token).send().await?;
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
        401 => return Err(ProviderError::api(
            "Seedr token is expired or invalid. Please reconnect your Seedr account.",
            "invalid_token.mp4",
        )),
        402 | 403 => return Err(ProviderError::api(
            "Seedr premium plan required for this operation.",
            "debrid_service_down_error.mp4",
        )),
        429 => return Err(ProviderError::api(
            "Seedr rate limit exceeded. Please try again later.",
            "api_error.mp4",
        )),
        500..=599 => return Err(ProviderError::api(
            "Seedr service is temporarily unavailable.",
            "debrid_service_down_error.mp4",
        )),
        _ => {}
    }
    let body: Value = resp.json().await.unwrap_or_default();
    if !status.is_success() {
        let msg = body["error_description"].as_str()
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
    let hash: String = rest.chars().take_while(|c| c.is_ascii_alphanumeric()).collect();
    if hash.len() >= 32 { Some(hash.to_lowercase()) } else { None }
}

// ─── Task helpers ─────────────────────────────────────────────────────────────

async fn list_tasks(http: &Client, token: &str) -> Result<Vec<Value>, ProviderError> {
    let resp = api_get(http, token, "/tasks").await?;
    Ok(resp["tasks"].as_array().cloned().unwrap_or_default())
}

fn task_info_hash(task: &Value) -> Option<String> {
    // Direct hash field
    if let Some(h) = task["hash"].as_str().filter(|s| !s.is_empty()) {
        return Some(h.to_lowercase());
    }
    // Extract from magnet URL in various possible locations
    let magnet = task["url"].as_str()
        .or_else(|| task["params"]["url"].as_str())
        .or_else(|| task["torrent_url"].as_str());
    if let Some(m) = magnet {
        return extract_info_hash_from_magnet(m);
    }
    None
}

fn task_is_complete(task: &Value) -> bool {
    let status = task["status"].as_str().unwrap_or("");
    // Seedr statuses: "seeding" = complete+seeding, "stopped" = paused, "queued", "downloading"
    if matches!(status, "seeding" | "stopped" | "idle" | "finished") {
        return true;
    }
    // Some API versions use progress 0.0-1.0
    if let Some(p) = task["progress"].as_f64() {
        if p >= 1.0 { return true; }
    }
    false
}

fn task_is_downloading(task: &Value) -> bool {
    let status = task["status"].as_str().unwrap_or("");
    matches!(status, "downloading" | "queued" | "active" | "pending" | "waiting")
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

async fn task_files(http: &Client, token: &str, task_id: i64) -> Result<Vec<(String, i64, i64)>, ProviderError> {
    let resp = api_get(http, token, &format!("/tasks/{task_id}/contents")).await?;
    let mut files = Vec::new();
    collect_files(&resp, &mut files);
    Ok(files)
}

async fn folder_files_recursive(http: &Client, token: &str, folder_id: i64) -> Result<Vec<(String, i64, i64)>, ProviderError> {
    let resp = api_get(http, token, &format!("/fs/folder/{folder_id}/contents")).await?;
    let mut files = Vec::new();
    collect_files(&resp, &mut files);
    // Recurse into subfolders
    if let Some(subfolders) = resp["folders"].as_array() {
        for sf in subfolders {
            if let Some(sfid) = sf["id"].as_i64() {
                let sub = Box::pin(folder_files_recursive(http, token, sfid)).await?;
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
        if let Some(f) = videos.iter().find(|(n, _, _)| n.to_lowercase().contains(&fname_lower)) {
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

async fn file_url(http: &Client, token: &str, file_id: i64) -> Result<String, ProviderError> {
    // Try video presentation first (streaming URL)
    if let Ok(resp) = api_get(http, token, &format!("/presentations/file/{file_id}/video")).await {
        if let Some(url) = resp["url"].as_str()
            .or_else(|| resp["stream_url"].as_str())
            .or_else(|| resp["link"]["url"].as_str())
        {
            return Ok(url.to_string());
        }
    }

    // Fall back to direct download URL
    let resp = api_get(http, token, &format!("/download/file/{file_id}/url")).await?;
    resp["url"].as_str()
        .or_else(|| resp["download_url"].as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| ProviderError::api(
            "Seedr returned no download URL for this file.",
            "api_error.mp4",
        ))
}

// ─── Internal resolution ──────────────────────────────────────────────────────

async fn resolve_from_task(
    http: &Client,
    token: &str,
    task_id: i64,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<String, ProviderError> {
    let files = task_files(http, token, task_id).await?;
    let sel = select_video(&files, filename, file_index, season, episode)
        .ok_or_else(|| ProviderError::api(
            "No matching video file found in Seedr torrent.",
            "no_matching_file.mp4",
        ))?;
    file_url(http, token, sel.2).await
}

async fn resolve_from_folder(
    http: &Client,
    token: &str,
    folder_id: i64,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<String, ProviderError> {
    let files = folder_files_recursive(http, token, folder_id).await?;
    let sel = select_video(&files, filename, file_index, season, episode)
        .ok_or_else(|| ProviderError::api(
            "No matching video file found in Seedr folder.",
            "no_matching_file.mp4",
        ))?;
    file_url(http, token, sel.2).await
}

// ─── Public entry point ───────────────────────────────────────────────────────

pub async fn get_video_url(
    http: &Client,
    token: &str,
    info_hash: &str,
    announce_list: &[String],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    _user_ip: Option<&str>,
) -> Result<String, ProviderError> {
    let token = resolve_token(token);
    let hash = info_hash.to_lowercase();
    let magnet = build_magnet(&hash, announce_list);

    // 1. Check active/completed tasks
    let tasks = list_tasks(http, &token).await?;

    for task in &tasks {
        if task_info_hash(task).as_deref() != Some(hash.as_str()) {
            continue;
        }
        if task_is_downloading(task) {
            return Err(ProviderError::api(
                "Torrent is still downloading in Seedr. Please try again later.",
                "torrent_not_downloaded.mp4",
            ));
        }
        if task_is_complete(task) {
            let task_id = task["id"].as_i64()
                .ok_or_else(|| ProviderError::api("Invalid task ID from Seedr", "api_error.mp4"))?;
            return resolve_from_task(http, &token, task_id, filename, file_index, season, episode).await;
        }
    }

    // 2. Check filesystem: folder named with info_hash (completed downloads may not stay in tasks)
    if let Ok(root) = api_get(http, &token, "/fs/root/contents").await {
        if let Some(folders) = root["folders"].as_array() {
            for folder in folders {
                let name = folder["name"].as_str().unwrap_or("").to_lowercase();
                if name == hash {
                    let folder_id = folder["id"].as_i64()
                        .ok_or_else(|| ProviderError::api("Invalid folder ID from Seedr", "api_error.mp4"))?;
                    return resolve_from_folder(http, &token, folder_id, filename, file_index, season, episode).await;
                }
            }
        }
    }

    // 3. Add new torrent task
    let add_resp = api_post(http, &token, "/tasks", &json!({"url": magnet, "name": hash}))
        .await
        .map_err(|e| ProviderError::api(
            format!("Failed to add torrent to Seedr: {e}"),
            "transfer_error.mp4",
        ))?;

    // Handle space/queue errors
    if let Some(err) = add_resp["error"].as_str() {
        let msg = match err {
            "not_enough_space" | "not_enough_space_added_to_wishlist" =>
                return Err(ProviderError::api("Not enough storage space in your Seedr account.", "not_enough_space.mp4")),
            "queue_full" | "queue_full_added_to_wishlist" =>
                return Err(ProviderError::api(
                    "Seedr download queue is full. Please wait for current downloads to finish.",
                    "queue_full.mp4",
                )),
            _ => format!("Seedr rejected the torrent: {err}"),
        };
        return Err(ProviderError::api(msg, "transfer_error.mp4"));
    }

    let task_id = add_resp["id"].as_i64()
        .or_else(|| add_resp["task"]["id"].as_i64())
        .or_else(|| add_resp["data"]["id"].as_i64())
        .ok_or_else(|| ProviderError::api(
            "Seedr did not return a task ID after adding torrent.",
            "transfer_error.mp4",
        ))?;

    // 4. Poll for completion
    for _ in 0..MAX_RETRIES {
        tokio::time::sleep(tokio::time::Duration::from_secs(RETRY_SECS)).await;

        let task_resp = api_get(http, &token, &format!("/tasks/{task_id}")).await
            .unwrap_or_default();

        let status = task_resp["status"].as_str().unwrap_or("");
        if matches!(status, "error" | "failed" | "dead") {
            return Err(ProviderError::api(
                "Seedr failed to download this torrent.",
                "torrent_error.mp4",
            ));
        }
        if task_is_complete(&task_resp) {
            return resolve_from_task(http, &token, task_id, filename, file_index, season, episode).await;
        }
    }

    Err(ProviderError::api(
        "Torrent is still downloading in Seedr. Please try again in a few minutes.",
        "torrent_not_downloaded.mp4",
    ))
}
