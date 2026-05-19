/// Real-Debrid streaming provider.
///
/// Token format:
///   - OAuth token (base64 "client_id:client_secret:refresh_code") — exchange for bearer
///   - Private token (raw, not valid base64) — used directly as Bearer
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use serde::Deserialize;
use serde_json::Value;

use crate::providers::{
    torrents::transport::{encode_form_body, MediaFlowForward},
    ProviderError,
};

const BASE_URL: &str = "https://api.real-debrid.com/rest/1.0";
const OAUTH_URL: &str = "https://api.real-debrid.com/oauth/v2";

// ─── Token decode ─────────────────────────────────────────────────────────────

enum TokenKind {
    Private(String),
    OAuth {
        client_id: String,
        client_secret: String,
        code: String,
    },
}

fn decode_token(token: &str) -> TokenKind {
    if let Ok(decoded) = B64.decode(token) {
        if let Ok(s) = std::str::from_utf8(&decoded) {
            let parts: Vec<&str> = s.splitn(3, ':').collect();
            if parts.len() == 3 {
                return TokenKind::OAuth {
                    client_id: parts[0].to_string(),
                    client_secret: parts[1].to_string(),
                    code: parts[2].to_string(),
                };
            }
        }
    }
    TokenKind::Private(token.to_string())
}

// ─── API error code mapping ───────────────────────────────────────────────────

fn map_error_code(code: i64) -> Option<(&'static str, &'static str)> {
    Some(match code {
        -1 => (
            "Real-Debrid internal error",
            "debrid_service_down_error.mp4",
        ),
        5 => ("Real-Debrid slow down", "too_many_requests.mp4"),
        7 => (
            "Real-Debrid resource not found",
            "torrent_not_downloaded.mp4",
        ),
        8..=15 => ("Real-Debrid authentication error", "invalid_token.mp4"),
        18 | 23 | 36 => (
            "Real-Debrid traffic limit reached",
            "exceed_remote_traffic_limit.mp4",
        ),
        21 => ("Real-Debrid too many active downloads", "torrent_limit.mp4"),
        22 => ("Real-Debrid IP not allowed", "ip_not_allowed.mp4"),
        24 => ("Real-Debrid file unavailable", "torrent_not_downloaded.mp4"),
        33 => (
            "Real-Debrid torrent already active",
            "torrent_not_downloaded.mp4",
        ),
        34 => ("Real-Debrid too many requests", "too_many_requests.mp4"),
        35 => ("Real-Debrid infringing file", "content_infringing.mp4"),
        _ if (16..=19).contains(&code) => {
            ("Real-Debrid hoster error", "debrid_service_down_error.mp4")
        }
        _ if (26..=32).contains(&code) => ("Real-Debrid transfer error", "transfer_error.mp4"),
        _ => return None,
    })
}

fn check_rd_error(body: &Value) -> Result<(), ProviderError> {
    if let Some(code) = body.get("error_code").and_then(|v| v.as_i64()) {
        let msg = body
            .get("error")
            .and_then(|v| v.as_str())
            .or_else(|| body.get("error_details").and_then(|v| v.as_str()))
            .unwrap_or("Unknown error");
        if let Some((label, file)) = map_error_code(code) {
            return Err(ProviderError::api(format!("{label}: {msg}"), file));
        }
        return Err(ProviderError::api(
            format!("Real-Debrid error {code}: {msg}"),
            "api_error.mp4",
        ));
    }
    Ok(())
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

async fn get_access_token(
    http: &reqwest::Client,
    client_id: &str,
    client_secret: &str,
    code: &str,
    user_ip: Option<&str>,
) -> Result<String, ProviderError> {
    let mut form = vec![
        ("client_id", client_id.to_string()),
        ("client_secret", client_secret.to_string()),
        ("code", code.to_string()),
        (
            "grant_type",
            "http://oauth.net/grant_type/device/1.0".to_string(),
        ),
    ];
    if let Some(ip) = user_ip {
        form.push(("ip", ip.to_string()));
    }

    let resp = http
        .post(format!("{OAUTH_URL}/token"))
        .form(&form)
        .send()
        .await?;

    let body: Value = resp.json().await?;
    check_rd_error(&body)?;
    body.get("access_token")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| {
            ProviderError::api(
                "Missing access_token in OAuth response",
                "invalid_token.mp4",
            )
        })
}

async fn rd_get(
    http: &reqwest::Client,
    bearer: &str,
    url: &str,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let resp = if let Some(fwd) = forward {
        // Include ip= in the destination URL so MediaFlow substitutes {mediaflow_ip}
        let dest = match user_ip {
            Some(ip) => {
                let sep = if url.contains('?') { '&' } else { '?' };
                format!("{url}{sep}ip={}", urlencoding::encode(ip))
            }
            None => url.to_string(),
        };
        fwd.get(http, &dest, bearer).await?
    } else {
        let mut req = http.get(url).bearer_auth(bearer);
        if let Some(ip) = user_ip {
            req = req.query(&[("ip", ip)]);
        }
        req.send().await?
    };
    if resp.status() == 204 {
        return Ok(Value::Null);
    }
    let body: Value = resp.json().await?;
    check_rd_error(&body)?;
    Ok(body)
}

async fn rd_post(
    http: &reqwest::Client,
    bearer: &str,
    url: &str,
    fields: &[(&str, &str)],
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let resp = if let Some(fwd) = forward {
        // Include ip= in the form body so MediaFlow substitutes {mediaflow_ip}
        let mut all_fields: Vec<(&str, &str)> = fields.to_vec();
        let owned_ip;
        if let Some(ip) = user_ip {
            owned_ip = ip.to_string();
            all_fields.push(("ip", &owned_ip));
        }
        let body = encode_form_body(&all_fields);
        fwd.post_form(http, url, bearer, body).await?
    } else {
        let mut form: Vec<(&str, &str)> = fields.to_vec();
        let owned_ip;
        if let Some(ip) = user_ip {
            owned_ip = ip.to_string();
            form.push(("ip", &owned_ip));
        }
        http.post(url)
            .bearer_auth(bearer)
            .form(&form)
            .send()
            .await?
    };
    if resp.status() == 204 {
        return Ok(Value::Null);
    }
    let body: Value = resp.json().await?;
    check_rd_error(&body)?;
    Ok(body)
}

async fn rd_delete(http: &reqwest::Client, bearer: &str, url: &str) -> Result<(), ProviderError> {
    http.delete(url).bearer_auth(bearer).send().await?;
    Ok(())
}

// ─── RD API operations ────────────────────────────────────────────────────────

async fn get_torrent_list(
    http: &reqwest::Client,
    bearer: &str,
    page: u32,
    limit: u32,
) -> Result<Vec<Value>, ProviderError> {
    let url = format!("{BASE_URL}/torrents?page={page}&limit={limit}");
    let body = rd_get(http, bearer, &url, None, None).await?;
    match body {
        Value::Array(arr) => Ok(arr),
        Value::Null => Ok(vec![]),
        other => {
            if other.get("error_code").is_some() {
                check_rd_error(&other)?;
            }
            Ok(vec![])
        }
    }
}

async fn find_torrent_by_hash(
    http: &reqwest::Client,
    bearer: &str,
    info_hash: &str,
) -> Result<Option<Value>, ProviderError> {
    const PAGE_SIZE: u32 = 100;
    const MAX_PAGES: u32 = 100;

    for page in 1..=MAX_PAGES {
        let page_data = get_torrent_list(http, bearer, page, PAGE_SIZE).await?;
        if page_data.is_empty() {
            break;
        }
        let first_page_unbounded = page == 1 && page_data.len() > PAGE_SIZE as usize;
        for t in &page_data {
            if t.get("hash")
                .and_then(|v| v.as_str())
                .map(str::to_lowercase)
                == Some(info_hash.to_lowercase())
            {
                return Ok(Some(t.clone()));
            }
        }
        if first_page_unbounded || page_data.len() < PAGE_SIZE as usize {
            break;
        }
    }
    Ok(None)
}

async fn get_torrent_info(
    http: &reqwest::Client,
    bearer: &str,
    torrent_id: &str,
) -> Result<Value, ProviderError> {
    let body = rd_get(
        http,
        bearer,
        &format!("{BASE_URL}/torrents/info/{torrent_id}"),
        None,
        None,
    )
    .await?;
    if body.is_null() {
        return Err(ProviderError::api(
            "Torrent not found",
            "torrent_not_downloaded.mp4",
        ));
    }
    Ok(body)
}

async fn add_magnet(
    http: &reqwest::Client,
    bearer: &str,
    magnet: &str,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let body = rd_post(
        http,
        bearer,
        &format!("{BASE_URL}/torrents/addMagnet"),
        &[("magnet", magnet)],
        user_ip,
        forward,
    )
    .await?;
    body.get("id")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| ProviderError::api("Failed to add magnet: missing id", "transfer_error.mp4"))
}

async fn select_files(
    http: &reqwest::Client,
    bearer: &str,
    torrent_id: &str,
    file_ids: &str, // "all" or comma-separated IDs
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    rd_post(
        http,
        bearer,
        &format!("{BASE_URL}/torrents/selectFiles/{torrent_id}"),
        &[("files", file_ids)],
        user_ip,
        forward,
    )
    .await?;
    Ok(())
}

async fn delete_torrent(
    http: &reqwest::Client,
    bearer: &str,
    torrent_id: &str,
) -> Result<(), ProviderError> {
    rd_delete(
        http,
        bearer,
        &format!("{BASE_URL}/torrents/delete/{torrent_id}"),
    )
    .await
}

/// Find and delete a single torrent by info_hash. Returns `true` if found and deleted, `false` if not found.
pub async fn delete_torrent_by_hash(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
) -> Result<bool, ProviderError> {
    let bearer = match decode_token(token) {
        TokenKind::Private(t) => t,
        TokenKind::OAuth {
            client_id,
            client_secret,
            code,
        } => get_access_token(http, &client_id, &client_secret, &code, None).await?,
    };
    match find_torrent_by_hash(http, &bearer, info_hash).await? {
        Some(t) => {
            if let Some(id) = t.get("id").and_then(|v| v.as_str()) {
                delete_torrent(http, &bearer, id).await?;
                Ok(true)
            } else {
                Ok(false)
            }
        }
        None => Ok(false),
    }
}

/// Delete ALL torrents from the user's Real-Debrid account (implements delete-all-watchlist).
pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    let bearer = match decode_token(token) {
        TokenKind::Private(t) => t,
        TokenKind::OAuth {
            client_id,
            client_secret,
            code,
        } => get_access_token(http, &client_id, &client_secret, &code, None).await?,
    };
    const PAGE_SIZE: u32 = 100;
    loop {
        let list = get_torrent_list(http, &bearer, 1, PAGE_SIZE).await?;
        if list.is_empty() {
            break;
        }
        for item in &list {
            if let Some(id) = item.get("id").and_then(|v| v.as_str()) {
                delete_torrent(http, &bearer, id).await.ok();
            }
        }
        if list.len() < PAGE_SIZE as usize {
            break;
        }
    }
    Ok(())
}

async fn unrestrict_link(
    http: &reqwest::Client,
    bearer: &str,
    link: &str,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    rd_post(
        http,
        bearer,
        &format!("{BASE_URL}/unrestrict/link"),
        &[("link", link)],
        user_ip,
        forward,
    )
    .await
}

async fn get_active_count(http: &reqwest::Client, bearer: &str) -> Result<Value, ProviderError> {
    rd_get(
        http,
        bearer,
        &format!("{BASE_URL}/torrents/activeCount"),
        None,
        None,
    )
    .await
}

// ─── Wait for torrent status ──────────────────────────────────────────────────

async fn wait_for_status(
    http: &reqwest::Client,
    bearer: &str,
    torrent_id: &str,
    target: &str,
    max_retries: u32,
    retry_interval_secs: u64,
) -> Result<Value, ProviderError> {
    for attempt in 0..max_retries {
        let info = get_torrent_info(http, bearer, torrent_id).await?;
        let status = info.get("status").and_then(|v| v.as_str()).unwrap_or("");
        if status.eq_ignore_ascii_case(target) {
            return Ok(info);
        }
        // Dead states — no point polling
        if matches!(status, "magnet_error" | "error" | "virus" | "dead") {
            return Err(ProviderError::api(
                format!("Torrent entered terminal status: {status}"),
                "transfer_error.mp4",
            ));
        }
        if attempt + 1 < max_retries {
            tokio::time::sleep(tokio::time::Duration::from_secs(retry_interval_secs)).await;
        }
    }
    Err(ProviderError::api(
        format!("Torrent did not reach '{target}' status after {max_retries} retries"),
        "torrent_not_downloaded.mp4",
    ))
}

// ─── Add new torrent (with check for active limit) ───────────────────────────

async fn add_new_torrent(
    http: &reqwest::Client,
    bearer: &str,
    magnet: &str,
    info_hash: &str,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let active = get_active_count(http, bearer).await?;
    if let (Some(limit), Some(nb)) = (
        active.get("limit").and_then(|v| v.as_i64()),
        active.get("nb").and_then(|v| v.as_i64()),
    ) {
        if limit == nb {
            return Err(ProviderError::api(
                "Torrent limit reached",
                "torrent_limit.mp4",
            ));
        }
    }
    if let Some(list) = active.get("list").and_then(|v| v.as_array()) {
        for item in list {
            if item.as_str().map(|s| s.to_lowercase()) == Some(info_hash.to_lowercase()) {
                return Err(ProviderError::api(
                    "Torrent is already downloading",
                    "torrent_not_downloaded.mp4",
                ));
            }
        }
    }

    // Try adding the magnet up to 2 times; fetch torrent info with retries.
    for create_attempt in 0..2u32 {
        let torrent_id = add_magnet(http, bearer, magnet, user_ip, forward).await?;
        for info_attempt in 0..3u32 {
            match get_torrent_info(http, bearer, &torrent_id).await {
                Ok(info) => return Ok(info),
                Err(e) => {
                    let msg = e.to_string().to_lowercase();
                    let is_unknown =
                        msg.contains("unknown_ressource") || msg.contains("resource not found");
                    if !is_unknown {
                        return Err(e);
                    }
                    if info_attempt < 2 {
                        tokio::time::sleep(tokio::time::Duration::from_millis(
                            500 * (info_attempt + 1) as u64,
                        ))
                        .await;
                        continue;
                    }
                    if create_attempt < 1 {
                        tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;
                        break;
                    }
                    return Err(ProviderError::api(
                        "Failed to fetch torrent info from Real-Debrid",
                        "transfer_error.mp4",
                    ));
                }
            }
        }
    }
    Err(ProviderError::api(
        "Failed to add magnet to Real-Debrid",
        "transfer_error.mp4",
    ))
}

// ─── File selection helpers ───────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct RdFile {
    id: i64,
    path: String,
    bytes: i64,
    selected: Option<i64>,
}

fn select_video_file_index(
    files: &[RdFile],
    _links_count: usize,
    filename: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    file_index: Option<i32>,
) -> usize {
    // 1. By exact filename match
    if let Some(name) = filename {
        let name_lower = name.to_lowercase();
        if let Some(idx) = files.iter().position(|f| {
            std::path::Path::new(&f.path)
                .file_name()
                .and_then(|n| n.to_str())
                .map(|n| n.to_lowercase() == name_lower)
                .unwrap_or(false)
        }) {
            return idx;
        }
    }

    // 2. By file_index hint from DB
    if let Some(fi) = file_index {
        if fi >= 0 && fi < files.len() as i32 {
            return fi as usize;
        }
    }

    // 3. For series: match S##E## in path
    if let (Some(s), Some(e)) = (season, episode) {
        let patterns = [format!("s{:02}e{:02}", s, e), format!("{:01}x{:02}", s, e)];
        if let Some(idx) = files.iter().position(|f| {
            let lower = f.path.to_lowercase();
            patterns.iter().any(|p| lower.contains(p))
        }) {
            return idx;
        }
    }

    // 4. Fallback: largest video file (mimics Python's `get_main_file`)
    let video_exts = ["mkv", "mp4", "avi", "webm", "mov", "flv", "wmv", "m4v"];
    files
        .iter()
        .enumerate()
        .filter(|(_, f)| {
            let ext = std::path::Path::new(&f.path)
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("")
                .to_lowercase();
            video_exts.contains(&ext.as_str())
        })
        .max_by_key(|(_, f)| f.bytes)
        .map(|(i, _)| i)
        .unwrap_or(0)
}

// ─── create_download_link ─────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
async fn create_download_link(
    http: &reqwest::Client,
    bearer: &str,
    magnet: &str,
    torrent_info: Value,
    filename: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    file_index: Option<i32>,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
    max_retries: u32,
    retry_interval: u64,
) -> Result<String, ProviderError> {
    let files: Vec<RdFile> = torrent_info
        .get("files")
        .and_then(|v| serde_json::from_value(v.clone()).ok())
        .unwrap_or_default();

    let links: Vec<String> = torrent_info
        .get("links")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let torrent_id = torrent_info
        .get("id")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    let selected_idx =
        select_video_file_index(&files, links.len(), filename, season, episode, file_index);

    let selected_files: Vec<&RdFile> = files.iter().filter(|f| f.selected == Some(1)).collect();
    let relevant_file = files.get(selected_idx);

    // If the file is not yet selected or selection count ≠ link count, re-select
    let needs_reselect = relevant_file.map(|f| f.selected != Some(1)).unwrap_or(true)
        || selected_files.len() != links.len();

    let (torrent_info, link_idx) = if needs_reselect {
        delete_torrent(http, bearer, torrent_id).await.ok();

        let new_id = add_magnet(http, bearer, magnet, user_ip, forward).await?;
        let info_wait = wait_for_status(
            http,
            bearer,
            &new_id,
            "waiting_files_selection",
            max_retries,
            retry_interval,
        )
        .await?;

        let files2: Vec<RdFile> = info_wait
            .get("files")
            .and_then(|v| serde_json::from_value(v.clone()).ok())
            .unwrap_or_default();
        let file_id = files2
            .get(selected_idx)
            .map(|f| f.id.to_string())
            .unwrap_or_else(|| "1".to_string());
        select_files(http, bearer, &new_id, &file_id, user_ip, forward).await?;
        let downloaded = wait_for_status(
            http,
            bearer,
            &new_id,
            "downloaded",
            max_retries,
            retry_interval,
        )
        .await?;
        (downloaded, 0usize)
    } else {
        let link_idx = selected_files
            .iter()
            .position(|f| {
                relevant_file
                    .map(|rf| std::ptr::eq(*f, rf))
                    .unwrap_or(false)
            })
            .unwrap_or(0);
        (torrent_info, link_idx)
    };

    let links: Vec<String> = torrent_info
        .get("links")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let link = links.get(link_idx).ok_or_else(|| {
        ProviderError::api("No download link available", "torrent_not_downloaded.mp4")
    })?;

    let unrestricted = unrestrict_link(http, bearer, link, user_ip, forward).await?;
    check_rd_error(&unrestricted)?;

    let mime = unrestricted
        .get("mimeType")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if !mime.is_empty() && !mime.starts_with("video") {
        return Err(ProviderError::api(
            format!("Requested file is not a video: {mime}"),
            "torrent_not_downloaded.mp4",
        ));
    }

    unrestricted
        .get("download")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| {
            ProviderError::api(
                "Missing download URL in unrestrict response",
                "api_error.mp4",
            )
        })
}

// ─── Public entry point ───────────────────────────────────────────────────────

/// Resolve a direct video URL from Real-Debrid for the given torrent.
///
/// Returns `(video_url, files)` — the file list is provided so the caller can
/// persist file metadata on first play (see `metadata_update`).
///
/// `announce_list` items are the tracker URLs (from the DB stream row).
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
) -> Result<
    (
        String,
        Vec<crate::providers::torrents::metadata_update::ProviderFile>,
    ),
    ProviderError,
> {
    const MAX_RETRIES: u32 = 5;
    const RETRY_INTERVAL: u64 = 5;

    // Resolve bearer token
    let bearer = match decode_token(token) {
        TokenKind::Private(t) => t,
        TokenKind::OAuth {
            client_id,
            client_secret,
            code,
        } => get_access_token(http, &client_id, &client_secret, &code, user_ip).await?,
    };

    // Build magnet from info_hash + trackers
    let trackers: String = announce_list
        .iter()
        .map(|t| format!("&tr={}", urlencoding::encode(t)))
        .collect();
    let magnet = format!("magnet:?xt=urn:btih:{info_hash}{trackers}");

    // Check if torrent already exists in user's RD library
    let torrent_info = match find_torrent_by_hash(http, &bearer, info_hash).await? {
        Some(existing) => {
            let status = existing
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if matches!(status, "magnet_error" | "error" | "virus" | "dead") {
                let torrent_id = existing.get("id").and_then(|v| v.as_str()).unwrap_or("");
                delete_torrent(http, &bearer, torrent_id).await.ok();
                add_new_torrent(http, &bearer, &magnet, info_hash, user_ip, forward).await?
            } else {
                existing
            }
        }
        None => add_new_torrent(http, &bearer, &magnet, info_hash, user_ip, forward).await?,
    };

    let torrent_id = torrent_info
        .get("id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let status = torrent_info
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    // Ensure files are selected and downloading
    let torrent_info = if !matches!(status.as_str(), "queued" | "downloading" | "downloaded") {
        let info_ws = wait_for_status(
            http,
            &bearer,
            &torrent_id,
            "waiting_files_selection",
            MAX_RETRIES,
            RETRY_INTERVAL,
        )
        .await?;
        let tid = info_ws
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or(&torrent_id)
            .to_string();
        select_files(http, &bearer, &tid, "all", user_ip, forward)
            .await
            .inspect_err(|_e| {
                let tid2 = tid.clone();
                let http2 = http.clone();
                let bearer2 = bearer.clone();
                // Fire-and-forget delete if select fails
                tokio::spawn(async move {
                    delete_torrent(&http2, &bearer2, &tid2).await.ok();
                });
            })?;
        wait_for_status(
            http,
            &bearer,
            &tid,
            "downloaded",
            MAX_RETRIES,
            RETRY_INTERVAL,
        )
        .await?
    } else if status != "downloaded" {
        wait_for_status(
            http,
            &bearer,
            &torrent_id,
            "downloaded",
            MAX_RETRIES,
            RETRY_INTERVAL,
        )
        .await?
    } else {
        torrent_info
    };

    // Extract file list before consuming torrent_info
    let provider_files: Vec<crate::providers::torrents::metadata_update::ProviderFile> =
        torrent_info
            .get("files")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .enumerate()
                    .filter_map(|(i, f)| {
                        Some(crate::providers::torrents::metadata_update::ProviderFile {
                            file_index: i as i32,
                            path: f.get("path").and_then(|v| v.as_str())?.to_string(),
                            bytes: f.get("bytes").and_then(|v| v.as_i64()).unwrap_or(0),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();

    let url = create_download_link(
        http,
        &bearer,
        &magnet,
        torrent_info,
        filename,
        season,
        episode,
        file_index,
        user_ip,
        forward,
        MAX_RETRIES,
        RETRY_INTERVAL,
    )
    .await?;

    Ok((url, provider_files))
}

// ─── List all downloaded torrents ────────────────────────────────────────────

/// A torrent that has been fully downloaded in the user's debrid account.
#[derive(Debug, Clone)]
pub struct DownloadedTorrent {
    pub info_hash: String,
    pub name: String,
    pub size: i64,
}

/// Return all fully-downloaded torrents in the user's RD account with name and size.
pub async fn list_downloaded_torrents(
    http: &reqwest::Client,
    token: &str,
) -> Result<Vec<DownloadedTorrent>, ProviderError> {
    let bearer = match decode_token(token) {
        TokenKind::Private(t) => t,
        TokenKind::OAuth {
            client_id,
            client_secret,
            code,
        } => get_access_token(http, &client_id, &client_secret, &code, None).await?,
    };

    const PAGE_SIZE: u32 = 100;
    const MAX_PAGES: u32 = 100;
    let mut result = Vec::new();

    for page in 1..=MAX_PAGES {
        let list = get_torrent_list(http, &bearer, page, PAGE_SIZE).await?;
        if list.is_empty() {
            break;
        }
        for t in &list {
            if t.get("status").and_then(|v| v.as_str()) == Some("downloaded") {
                let hash = match t.get("hash").and_then(|v| v.as_str()) {
                    Some(h) => h.to_lowercase(),
                    None => continue,
                };
                let name = t
                    .get("filename")
                    .and_then(|v| v.as_str())
                    .unwrap_or(&hash)
                    .to_string();
                let size = t.get("bytes").and_then(|v| v.as_i64()).unwrap_or(0);
                result.push(DownloadedTorrent {
                    info_hash: hash,
                    name,
                    size,
                });
            }
        }
        if list.len() < PAGE_SIZE as usize {
            break;
        }
    }
    Ok(result)
}

// ─── List all downloaded hashes ──────────────────────────────────────────────

/// Return all info_hashes that are fully downloaded in the user's RD account.
pub async fn list_downloaded_hashes(
    http: &reqwest::Client,
    token: &str,
) -> Result<Vec<String>, ProviderError> {
    let bearer = match decode_token(token) {
        TokenKind::Private(t) => t,
        TokenKind::OAuth {
            client_id,
            client_secret,
            code,
        } => get_access_token(http, &client_id, &client_secret, &code, None).await?,
    };

    const PAGE_SIZE: u32 = 100;
    const MAX_PAGES: u32 = 100;
    let mut result = Vec::new();

    for page in 1..=MAX_PAGES {
        let list = get_torrent_list(http, &bearer, page, PAGE_SIZE).await?;
        if list.is_empty() {
            break;
        }
        for t in &list {
            if t.get("status").and_then(|v| v.as_str()) == Some("downloaded") {
                if let Some(h) = t.get("hash").and_then(|v| v.as_str()) {
                    result.push(h.to_lowercase());
                }
            }
        }
        if list.len() < PAGE_SIZE as usize {
            break;
        }
    }
    Ok(result)
}

// ─── Debrid cache check ───────────────────────────────────────────────────────

/// Check which hashes are downloaded in the user's Real-Debrid account.
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    use std::collections::HashSet;
    const PAGE_SIZE: u32 = 100;
    const MAX_PAGES: u32 = 50;

    let bearer = match decode_token(token) {
        TokenKind::Private(t) => t,
        TokenKind::OAuth {
            client_id,
            client_secret,
            code,
        } => match get_access_token(http, &client_id, &client_secret, &code, None).await {
            Ok(t) => t,
            Err(_) => return vec![],
        },
    };

    let hash_set: HashSet<String> = hashes.iter().map(|h| h.to_lowercase()).collect();
    let mut found = Vec::new();

    for page in 1..=MAX_PAGES {
        let url = format!("{BASE_URL}/torrents?page={page}&limit={PAGE_SIZE}");
        let resp = match http.get(&url).bearer_auth(&bearer).send().await {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!("realdebrid torrents page {page}: {e}");
                break;
            }
        };
        let body: serde_json::Value = match resp.json().await {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!("realdebrid torrents json page {page}: {e}");
                break;
            }
        };
        let arr = match body.as_array() {
            Some(a) if !a.is_empty() => a.clone(),
            _ => break,
        };
        for t in &arr {
            if t.get("status").and_then(|v| v.as_str()) == Some("downloaded") {
                if let Some(h) = t.get("hash").and_then(|v| v.as_str()) {
                    let lower = h.to_lowercase();
                    if hash_set.contains(&lower) {
                        found.push(lower);
                    }
                }
            }
        }
        if found.len() >= hashes.len() || arr.len() < PAGE_SIZE as usize {
            break;
        }
    }
    found
}
