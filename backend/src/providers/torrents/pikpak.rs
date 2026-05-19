/// PikPak streaming provider.
///
/// Token format: base64-encoded JSON {"access_token": "...", "refresh_token": "..."}
///
/// Auth: Bearer {access_token} on every request.
/// Token refresh: POST /v1/auth/token — no captcha required.
///
/// API hosts:
///   Drive: api-drive.mypikpak.com
///   User:  user.mypikpak.com
use base64::{engine::general_purpose::STANDARD, Engine};
use reqwest::Client;
use serde_json::{json, Value};

use crate::providers::{
    torrents::transport::{append_query, MediaFlowForward},
    ProviderError,
};

const API_HOST: &str = "api-drive.mypikpak.com";
const USER_HOST: &str = "user.mypikpak.com";
const CLIENT_ID: &str = "YNxT9w7GMdWvEOKa";
const CLIENT_SECRET: &str = "dbw2OtmVEeuUvIptb1Coyg";

const MAX_RETRIES: u32 = 3;
const RETRY_SECS: u64 = 5;

static VIDEO_EXTS: &[&str] = &["mkv", "mp4", "avi", "webm", "mov", "flv", "m4v", "wmv"];

// ─── Token ────────────────────────────────────────────────────────────────────

struct Tokens {
    access_token: String,
    refresh_token: String,
}

fn decode_token(raw: &str) -> Result<Tokens, ProviderError> {
    let decoded = STANDARD
        .decode(raw.trim())
        .map_err(|_| ProviderError::api("Invalid PikPak token format.", "invalid_token.mp4"))?;
    let s = String::from_utf8(decoded)
        .map_err(|_| ProviderError::api("Invalid PikPak token encoding.", "invalid_token.mp4"))?;
    let v: Value = serde_json::from_str(&s)
        .map_err(|_| ProviderError::api("Invalid PikPak token JSON.", "invalid_token.mp4"))?;
    let access = v["access_token"]
        .as_str()
        .ok_or_else(|| {
            ProviderError::api("PikPak token missing access_token.", "invalid_token.mp4")
        })?
        .to_string();
    let refresh = v["refresh_token"]
        .as_str()
        .ok_or_else(|| {
            ProviderError::api("PikPak token missing refresh_token.", "invalid_token.mp4")
        })?
        .to_string();
    Ok(Tokens {
        access_token: access,
        refresh_token: refresh,
    })
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

fn drive_url(path: &str) -> String {
    format!("https://{API_HOST}{path}")
}

fn user_url(path: &str) -> String {
    format!("https://{USER_HOST}{path}")
}

fn build_headers(access_token: &str) -> reqwest::header::HeaderMap {
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert(
        reqwest::header::AUTHORIZATION,
        format!("Bearer {access_token}").parse().unwrap(),
    );
    headers.insert(
        reqwest::header::CONTENT_TYPE,
        "application/json; charset=utf-8".parse().unwrap(),
    );
    // Minimal User-Agent (no captcha token, so basic browser UA)
    headers.insert(
        reqwest::header::USER_AGENT,
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36".parse().unwrap(),
    );
    headers
}

/// Attempt to refresh the access token. Returns updated Tokens on success.
async fn refresh_tokens(http: &Client, refresh_token: &str) -> Result<Tokens, ProviderError> {
    let url = user_url("/v1/auth/token");
    let body = json!({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    });
    let resp = http.post(&url).json(&body).send().await?;
    let data: Value = resp.json().await.unwrap_or_default();
    if data.get("error").is_some() {
        return Err(ProviderError::api(
            "PikPak token is expired or invalid. Please reconnect your PikPak account.",
            "invalid_token.mp4",
        ));
    }
    let access = data["access_token"]
        .as_str()
        .ok_or_else(|| ProviderError::api("PikPak token refresh failed.", "invalid_token.mp4"))?
        .to_string();
    let refresh = data["refresh_token"]
        .as_str()
        .unwrap_or(refresh_token)
        .to_string();
    Ok(Tokens {
        access_token: access,
        refresh_token: refresh,
    })
}

/// Make a GET request, refreshing token once on error_code 16.
async fn api_get(
    http: &Client,
    tokens: &mut Tokens,
    path: &str,
    params: &[(&str, &str)],
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = drive_url(path);
    let data: Value = if let Some(fwd) = forward {
        let dest = append_query(&url, params);
        fwd.get(http, &dest, &tokens.access_token)
            .await?
            .json()
            .await
            .unwrap_or_default()
    } else {
        http.get(&url)
            .headers(build_headers(&tokens.access_token))
            .query(params)
            .send()
            .await?
            .json()
            .await
            .unwrap_or_default()
    };
    if data.get("error_code").and_then(|v| v.as_i64()) == Some(16) {
        // Token expired — refresh and retry once
        *tokens = refresh_tokens(http, &tokens.refresh_token).await?;
        let data2: Value = if let Some(fwd) = forward {
            let dest = append_query(&url, params);
            fwd.get(http, &dest, &tokens.access_token)
                .await?
                .json()
                .await
                .unwrap_or_default()
        } else {
            http.get(&url)
                .headers(build_headers(&tokens.access_token))
                .query(params)
                .send()
                .await?
                .json()
                .await
                .unwrap_or_default()
        };
        return check_api_error(data2);
    }
    check_api_error(data)
}

/// Make a POST request, refreshing token once on error_code 16.
async fn api_post(
    http: &Client,
    tokens: &mut Tokens,
    path: &str,
    body: &Value,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = drive_url(path);
    let data: Value = if let Some(fwd) = forward {
        fwd.post_json(http, &url, &tokens.access_token, body.to_string())
            .await?
            .json()
            .await
            .unwrap_or_default()
    } else {
        http.post(&url)
            .headers(build_headers(&tokens.access_token))
            .json(body)
            .send()
            .await?
            .json()
            .await
            .unwrap_or_default()
    };
    if data.get("error_code").and_then(|v| v.as_i64()) == Some(16) {
        *tokens = refresh_tokens(http, &tokens.refresh_token).await?;
        let data2: Value = if let Some(fwd) = forward {
            fwd.post_json(http, &url, &tokens.access_token, body.to_string())
                .await?
                .json()
                .await
                .unwrap_or_default()
        } else {
            http.post(&url)
                .headers(build_headers(&tokens.access_token))
                .json(body)
                .send()
                .await?
                .json()
                .await
                .unwrap_or_default()
        };
        return check_api_error(data2);
    }
    check_api_error(data)
}

fn check_api_error(data: Value) -> Result<Value, ProviderError> {
    if let Some(err) = data.get("error") {
        let msg = data["error_description"]
            .as_str()
            .unwrap_or_else(|| err.as_str().unwrap_or("PikPak API error"));
        let msg_lower = msg.to_lowercase();
        let vf = if msg_lower.contains("invalid")
            && (msg_lower.contains("token") || msg_lower.contains("account"))
        {
            "invalid_token.mp4"
        } else {
            "api_error.mp4"
        };
        return Err(ProviderError::api(msg.to_string(), vf));
    }
    Ok(data)
}

// ─── Task helpers ─────────────────────────────────────────────────────────────

/// Fetch offline tasks. Phases: PHASE_TYPE_RUNNING, PHASE_TYPE_ERROR, PHASE_TYPE_COMPLETE, PHASE_TYPE_PENDING.
async fn offline_list(
    http: &Client,
    tokens: &mut Tokens,
    phases: &[&str],
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<Value>, ProviderError> {
    let filters =
        serde_json::to_string(&json!({"phase": {"in": phases.join(",")}})).unwrap_or_default();
    let data = api_get(
        http,
        tokens,
        "/drive/v1/tasks",
        &[
            ("type", "offline"),
            ("thumbnail_size", "SIZE_SMALL"),
            ("limit", "10000"),
            ("filters", &filters),
            ("with", "reference_resource"),
        ],
        forward,
    )
    .await?;
    Ok(data["tasks"].as_array().cloned().unwrap_or_default())
}

fn task_has_info_hash(task: &Value, info_hash: &str) -> bool {
    let url = task["params"]["url"].as_str().unwrap_or("");
    url.to_lowercase().contains(info_hash)
}

fn task_phase(task: &Value) -> &str {
    task["phase"].as_str().unwrap_or("")
}

fn task_is_complete(task: &Value) -> bool {
    task_phase(task) == "PHASE_TYPE_COMPLETE"
        || task["progress"]
            .as_str()
            .map(|p| p == "100")
            .unwrap_or(false)
}

fn task_is_downloading(task: &Value) -> bool {
    matches!(
        task_phase(task),
        "PHASE_TYPE_RUNNING" | "PHASE_TYPE_PENDING"
    )
}

fn task_is_error(task: &Value) -> bool {
    task_phase(task) == "PHASE_TYPE_ERROR"
}

// ─── File helpers ─────────────────────────────────────────────────────────────

fn is_video(name: &str) -> bool {
    let lower = name.to_lowercase();
    VIDEO_EXTS.iter().any(|e| lower.ends_with(&format!(".{e}")))
}

/// Recursively collect all video files from a folder.
async fn collect_folder_videos(
    http: &Client,
    tokens: &mut Tokens,
    folder_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<(String, i64, String)>, ProviderError> {
    let filters = serde_json::to_string(
        &json!({"trashed": {"eq": false}, "phase": {"eq": "PHASE_TYPE_COMPLETE"}}),
    )
    .unwrap_or_default();
    let data = api_get(
        http,
        tokens,
        "/drive/v1/files",
        &[
            ("parent_id", folder_id),
            ("thumbnail_size", "SIZE_MEDIUM"),
            ("limit", "100"),
            ("with_audit", "true"),
            ("filters", &filters),
        ],
        forward,
    )
    .await?;

    let mut results = Vec::new();
    for item in data["files"].as_array().iter().flat_map(|a| a.iter()) {
        let kind = item["kind"].as_str().unwrap_or("");
        let name = item["name"].as_str().unwrap_or("").to_string();
        let id = item["id"].as_str().unwrap_or("").to_string();

        if kind == "drive#folder" {
            if !id.is_empty() {
                let sub = Box::pin(collect_folder_videos(http, tokens, &id, forward)).await?;
                results.extend(sub);
            }
        } else if is_video(&name) && !id.is_empty() {
            let size = item["size"]
                .as_str()
                .and_then(|s| s.parse().ok())
                .unwrap_or(0i64);
            results.push((name, size, id));
        }
    }
    Ok(results)
}

fn select_video<'a>(
    files: &'a [(String, i64, String)],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<&'a (String, i64, String)> {
    if files.is_empty() {
        return None;
    }

    if let Some(idx) = file_index {
        if let Some(f) = files.get(idx as usize) {
            return Some(f);
        }
    }

    if let Some(fname) = filename {
        let fname_lower = fname.to_lowercase();
        if let Some(f) = files
            .iter()
            .find(|(n, _, _)| n.to_lowercase().contains(&fname_lower))
        {
            return Some(f);
        }
    }

    if let (Some(s), Some(e)) = (season, episode) {
        let patterns = [
            format!("s{s:02}e{e:02}"),
            format!("{s}x{e:02}"),
            format!("{s:02}x{e:02}"),
        ];
        for f in files.iter() {
            let lower = f.0.to_lowercase();
            if patterns.iter().any(|p| lower.contains(p.as_str())) {
                return Some(f);
            }
        }
    }

    files.iter().max_by_key(|(_, sz, _)| sz)
}

// ─── Download URL ─────────────────────────────────────────────────────────────

async fn get_download_url(
    http: &Client,
    tokens: &mut Tokens,
    file_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let data = api_get(
        http,
        tokens,
        &format!("/drive/v1/files/{file_id}"),
        &[],
        forward,
    )
    .await?;

    // Prefer streaming URL from medias array
    if let Some(medias) = data["medias"].as_array() {
        for media in medias {
            if let Some(url) = media["link"]["url"].as_str().filter(|s| !s.is_empty()) {
                return Ok(url.to_string());
            }
        }
    }

    // Fall back to web content link
    data["web_content_link"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            ProviderError::api(
                "PikPak returned no download URL for this file.",
                "api_error.mp4",
            )
        })
}

// ─── My Pack folder lookup ────────────────────────────────────────────────────

async fn get_my_pack_folder_id(
    http: &Client,
    tokens: &mut Tokens,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let filters = serde_json::to_string(
        &json!({"trashed": {"eq": false}, "phase": {"eq": "PHASE_TYPE_COMPLETE"}}),
    )
    .unwrap_or_default();
    let data = api_get(
        http,
        tokens,
        "/drive/v1/files",
        &[
            ("thumbnail_size", "SIZE_MEDIUM"),
            ("limit", "100"),
            ("with_audit", "true"),
            ("filters", &filters),
        ],
        forward,
    )
    .await?;

    data["files"]
        .as_array()
        .and_then(|files| {
            files.iter().find(|f| {
                f["name"].as_str() == Some("My Pack") && f["kind"].as_str() == Some("drive#folder")
            })
        })
        .and_then(|f| f["id"].as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| ProviderError::api("PikPak 'My Pack' folder not found.", "api_error.mp4"))
}

/// Find item in My Pack folder whose params.url contains info_hash.
async fn find_torrent_item(
    http: &Client,
    tokens: &mut Tokens,
    my_pack_id: &str,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<Value>, ProviderError> {
    let filters = serde_json::to_string(
        &json!({"trashed": {"eq": false}, "phase": {"eq": "PHASE_TYPE_COMPLETE"}}),
    )
    .unwrap_or_default();
    let data = api_get(
        http,
        tokens,
        "/drive/v1/files",
        &[
            ("parent_id", my_pack_id),
            ("thumbnail_size", "SIZE_MEDIUM"),
            ("limit", "1000"),
            ("with_audit", "true"),
            ("filters", &filters),
        ],
        forward,
    )
    .await?;

    let item = data["files"]
        .as_array()
        .and_then(|files| {
            files.iter().find(|f| {
                f["params"]["url"]
                    .as_str()
                    .map(|u| u.to_lowercase().contains(info_hash))
                    .unwrap_or(false)
            })
        })
        .cloned();
    Ok(item)
}

// ─── Error handling ───────────────────────────────────────────────────────────

fn map_pikpak_error(msg: &str) -> ProviderError {
    let lower = msg.to_lowercase();
    if lower.contains("review") {
        return ProviderError::api(
            "PikPak account is under review. Please complete verification in PikPak.",
            "invalid_credentials.mp4",
        );
    }
    if lower.contains("invalid username") || lower.contains("invalid password") {
        return ProviderError::api("Invalid PikPak credentials.", "invalid_credentials.mp4");
    }
    if lower.contains("invalid token") || lower.contains("unauthorized") {
        return ProviderError::api(
            "PikPak token is invalid. Please reconnect.",
            "invalid_token.mp4",
        );
    }
    if lower.contains("too frequent") || lower.contains("try again later") {
        return ProviderError::api(
            "PikPak is temporarily unavailable. Please try again later.",
            "debrid_service_down_error.mp4",
        );
    }
    if lower.contains("daily") || lower.contains("free usage") || lower.contains("free transfers") {
        return ProviderError::api(
            "PikPak daily download limit reached.",
            "daily_download_limit.mp4",
        );
    }
    if lower.contains("storage") || lower.contains("not enough space") {
        return ProviderError::api(
            "Not enough storage space in your PikPak account.",
            "not_enough_space.mp4",
        );
    }
    ProviderError::api(
        format!("PikPak error: {msg}"),
        "debrid_service_down_error.mp4",
    )
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
    _user_ip: Option<&str>,
    forward: Option<&crate::providers::torrents::transport::MediaFlowForward>,
) -> Result<String, ProviderError> {
    let mut tokens = decode_token(token)?;
    let hash = info_hash.to_lowercase();

    // 1. Check offline tasks (running + error phases)
    let tasks = offline_list(
        http,
        &mut tokens,
        &["PHASE_TYPE_RUNNING", "PHASE_TYPE_ERROR"],
        forward,
    )
    .await?;
    for task in &tasks {
        if !task_has_info_hash(task, &hash) {
            continue;
        }
        if task_is_error(task) {
            let msg = task["message"]
                .as_str()
                .unwrap_or("Error downloading torrent");
            return Err(map_pikpak_error(msg));
        }
        if task_is_downloading(task) {
            return Err(ProviderError::api(
                "Torrent is still downloading in PikPak. Please try again later.",
                "torrent_not_downloaded.mp4",
            ));
        }
    }

    // 2. Find file in My Pack folder
    let my_pack_id = get_my_pack_folder_id(http, &mut tokens, forward).await?;
    let torrent_item = find_torrent_item(http, &mut tokens, &my_pack_id, &hash, forward).await?;

    if let Some(item) = torrent_item {
        let file_id = item["id"].as_str().unwrap_or("").to_string();
        let kind = item["kind"].as_str().unwrap_or("");
        let file_name = item["name"].as_str().unwrap_or("").to_string();

        let selected_id = if kind == "drive#folder" {
            // Collect all video files from folder
            let videos = collect_folder_videos(http, &mut tokens, &file_id, forward).await?;
            select_video(&videos, filename, file_index, season, episode)
                .ok_or_else(|| {
                    ProviderError::api(
                        "No matching video file found in PikPak folder.",
                        "no_matching_file.mp4",
                    )
                })?
                .2
                .clone()
        } else if is_video(&file_name) {
            // Single file torrent
            file_id
        } else {
            return Err(ProviderError::api(
                "No video file found in PikPak torrent.",
                "no_matching_file.mp4",
            ));
        };

        return get_download_url(http, &mut tokens, &selected_id, forward).await;
    }

    // 3. Add magnet and wait
    let magnet = {
        let trackers = announce_list
            .iter()
            .map(|t| format!("tr={}", urlencoding::encode(t)))
            .collect::<Vec<_>>()
            .join("&");
        if trackers.is_empty() {
            format!("magnet:?xt=urn:btih:{hash}")
        } else {
            format!("magnet:?xt=urn:btih:{hash}&{trackers}")
        }
    };

    let add_resp = api_post(
        http,
        &mut tokens,
        "/drive/v1/files",
        &json!({
            "kind": "drive#file",
            "upload_type": "UPLOAD_TYPE_URL",
            "url": {"url": magnet},
            "folder_type": "DOWNLOAD",
        }),
        forward,
    )
    .await
    .map_err(|e| {
        let msg = e.to_string().to_lowercase();
        if msg.contains("daily") || msg.contains("free usage") {
            ProviderError::api(
                "PikPak daily download limit reached.",
                "daily_download_limit.mp4",
            )
        } else if msg.contains("storage") || msg.contains("not enough space") {
            ProviderError::api(
                "Not enough storage space in your PikPak account.",
                "not_enough_space.mp4",
            )
        } else {
            ProviderError::api(
                format!("Failed to add torrent to PikPak: {e}"),
                "transfer_error.mp4",
            )
        }
    })?;

    // Check for inline errors in the add response
    if let Some(err) = add_resp["error"].as_str() {
        return Err(map_pikpak_error(err));
    }

    // 4. Poll for completion
    for _ in 0..MAX_RETRIES {
        tokio::time::sleep(tokio::time::Duration::from_secs(RETRY_SECS)).await;

        let tasks = offline_list(
            http,
            &mut tokens,
            &[
                "PHASE_TYPE_RUNNING",
                "PHASE_TYPE_ERROR",
                "PHASE_TYPE_COMPLETE",
            ],
            forward,
        )
        .await
        .unwrap_or_default();

        let task = tasks.iter().find(|t| task_has_info_hash(t, &hash));
        if let Some(task) = task {
            if task_is_error(task) {
                let msg = task["message"]
                    .as_str()
                    .unwrap_or("Error downloading torrent");
                return Err(map_pikpak_error(msg));
            }
            if task_is_complete(task) {
                // Re-check My Pack folder
                if let Ok(Some(item)) =
                    find_torrent_item(http, &mut tokens, &my_pack_id, &hash, forward).await
                {
                    let file_id = item["id"].as_str().unwrap_or("").to_string();
                    let kind = item["kind"].as_str().unwrap_or("");
                    let file_name = item["name"].as_str().unwrap_or("").to_string();

                    let selected_id = if kind == "drive#folder" {
                        let videos =
                            collect_folder_videos(http, &mut tokens, &file_id, forward).await?;
                        select_video(&videos, filename, file_index, season, episode)
                            .ok_or_else(|| {
                                ProviderError::api(
                                    "No matching video file found in PikPak folder.",
                                    "no_matching_file.mp4",
                                )
                            })?
                            .2
                            .clone()
                    } else if is_video(&file_name) {
                        file_id
                    } else {
                        return Err(ProviderError::api(
                            "No video file found in PikPak torrent.",
                            "no_matching_file.mp4",
                        ));
                    };

                    return get_download_url(http, &mut tokens, &selected_id, forward).await;
                }
            }
        }
    }

    Err(ProviderError::api(
        "Torrent is still downloading in PikPak. Please try again in a few minutes.",
        "torrent_not_downloaded.mp4",
    ))
}

/// Delete ALL items in the PikPak My Pack folder.
pub async fn delete_all_torrents(http: &Client, token: &str) -> Result<(), ProviderError> {
    let mut tokens = decode_token(token)?;
    let my_pack_id = get_my_pack_folder_id(http, &mut tokens, None).await?;

    let filters =
        serde_json::to_string(&serde_json::json!({"trashed": {"eq": false}})).unwrap_or_default();
    let data = api_get(
        http,
        &mut tokens,
        "/drive/v1/files",
        &[
            ("parent_id", my_pack_id.as_str()),
            ("limit", "1000"),
            ("filters", &filters),
        ],
        None,
    )
    .await?;

    let ids: Vec<String> = data["files"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|f| f["id"].as_str().map(str::to_string))
        .collect();

    if !ids.is_empty() {
        let body = serde_json::json!({ "ids": ids });
        api_post(http, &mut tokens, "/drive/v1/files/trash", &body, None)
            .await
            .ok();
    }
    Ok(())
}

/// Delete the item matching `info_hash` from PikPak My Pack folder.
/// Returns `true` if found and trashed, `false` if not found.
pub async fn delete_torrent_by_hash(
    http: &Client,
    token: &str,
    info_hash: &str,
) -> Result<bool, ProviderError> {
    let mut tokens = decode_token(token)?;
    let hash = info_hash.to_lowercase();
    let my_pack_id = get_my_pack_folder_id(http, &mut tokens, None).await?;
    let item = find_torrent_item(http, &mut tokens, &my_pack_id, &hash, None).await?;

    match item {
        None => Ok(false),
        Some(item) => {
            let file_id = item["id"].as_str().unwrap_or("").to_string();
            if !file_id.is_empty() {
                let body = serde_json::json!({ "ids": [file_id] });
                api_post(http, &mut tokens, "/drive/v1/files/trash", &body, None)
                    .await
                    .ok();
            }
            Ok(true)
        }
    }
}
