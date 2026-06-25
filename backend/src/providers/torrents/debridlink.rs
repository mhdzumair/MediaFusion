/// Debrid-Link streaming provider.
///
/// Token format:
///   - Base64-encoded refresh_token (URL_SAFE_NO_PAD) → exchange for access_token via OAuth
///   - Plain private token → used directly as Bearer
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD as B64};
use serde_json::{Value, json};

use crate::providers::{
    ProviderError,
    file_selection::select_debrid_file_index,
    response_json,
    torrents::transport::{MediaFlowForward, append_query},
};

const BASE_URL: &str = "https://debrid-link.com/api/v2";
const CLIENT_ID: &str = "RyrV22FOg30DsxjYPziRKA";

// ─── Token detection ──────────────────────────────────────────────────────────

enum TokenKind {
    /// A plain private API token — used directly as Bearer.
    Private(String),
    /// A base64-encoded refresh_token that must be exchanged.
    Refresh(String),
}

/// Detect whether `token` is a base64-encoded refresh token or a raw private key.
///
/// Heuristic: if base64-decoding succeeds, the result is valid UTF-8, the
/// decoded string contains only printable ASCII (no spaces, no braces), and is
/// at least 20 characters long, we treat it as a refresh token.
fn decode_token(token: &str) -> TokenKind {
    if let Ok(bytes) = B64.decode(token)
        && let Ok(s) = std::str::from_utf8(&bytes) {
            let trimmed = s.trim();
            let is_printable_ascii = trimmed.bytes().all(|b| (0x21..0x7f).contains(&b));
            let no_json_chars = !trimmed.contains('{') && !trimmed.contains(' ');
            if is_printable_ascii && no_json_chars && trimmed.len() >= 20 {
                return TokenKind::Refresh(trimmed.to_string());
            }
        }
    TokenKind::Private(token.to_string())
}

// ─── OAuth ────────────────────────────────────────────────────────────────────

async fn exchange_refresh_token(
    http: &reqwest::Client,
    refresh_token: &str,
) -> Result<String, ProviderError> {
    let body = json!({
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    });

    let resp = http
        .post("https://debrid-link.com/api/oauth/token")
        .json(&body)
        .send()
        .await?;

    let json: Value = response_json(resp, "dl_refresh_token").await?;
    check_dl_error(&json)?;

    json.get("access_token")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| {
            ProviderError::api(
                "Missing access_token in Debrid-Link OAuth response",
                "invalid_token.mp4",
            )
        })
}

async fn resolve_bearer(http: &reqwest::Client, token: &str) -> Result<String, ProviderError> {
    match decode_token(token) {
        TokenKind::Private(t) => Ok(t),
        TokenKind::Refresh(rt) => exchange_refresh_token(http, &rt).await,
    }
}

// ─── Error mapping ────────────────────────────────────────────────────────────

fn map_dl_error(code: &str) -> Option<(&'static str, &'static str)> {
    Some(match code {
        "badToken" | "expired_token" => ("Invalid token", "invalid_token.mp4"),
        "freeServerOverload" => ("Debrid-Link free servers overloaded", "need_premium.mp4"),
        "server_error" | "notDebrid" => {
            ("Debrid-Link server error", "debrid_service_down_error.mp4")
        }
        "maxLink" | "maxData" | "maxTorrent" | "maxLinkHost" | "maxDataHost" => (
            "Debrid-Link daily limit reached",
            "daily_download_limit.mp4",
        ),
        "floodDetected" => ("Flood detected", "too_many_requests.mp4"),
        _ => return None,
    })
}

fn check_dl_error(body: &Value) -> Result<(), ProviderError> {
    // Debrid-Link uses `{"success": false, "error": "errorCode"}` on failure
    let success = body
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    if !success
        && let Some(err) = body.get("error").and_then(|v| v.as_str()) {
            if let Some((msg, file)) = map_dl_error(err) {
                return Err(ProviderError::api(msg, file));
            }
            return Err(ProviderError::api(
                format!("Debrid-Link API error: {err}"),
                "api_error.mp4",
            ));
        }
    Ok(())
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

async fn dl_get(
    http: &reqwest::Client,
    bearer: &str,
    path: &str,
    query: &[(&str, &str)],
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let base_url = format!("{BASE_URL}{path}");
    let dest = if query.is_empty() {
        base_url
    } else {
        let pairs: Vec<(&str, &str)> = query.to_vec();
        append_query(&base_url, &pairs)
    };
    let resp = if let Some(fwd) = forward {
        fwd.get(http, &dest, bearer).await?
    } else {
        http.get(&dest).bearer_auth(bearer).send().await?
    };
    let body: Value = response_json(resp, "dl_get").await?;
    check_dl_error(&body)?;
    Ok(body)
}

async fn dl_post(
    http: &reqwest::Client,
    bearer: &str,
    path: &str,
    payload: Value,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let resp = if let Some(fwd) = forward {
        fwd.post_json(http, &url, bearer, payload.to_string())
            .await?
    } else {
        http.post(&url)
            .bearer_auth(bearer)
            .json(&payload)
            .send()
            .await?
    };
    let body: Value = response_json(resp, "dl_post").await?;
    check_dl_error(&body)?;
    Ok(body)
}

async fn dl_post_multipart(
    http: &reqwest::Client,
    bearer: &str,
    path: &str,
    torrent_bytes: &[u8],
    torrent_name: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE_URL}{path}");
    let filename = torrent_name
        .filter(|n| !n.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| "torrent.torrent".to_string());
    let boundary = "mediafusion_debridlink_upload";

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

    let resp = if let Some(fwd) = forward {
        fwd.post_raw(http, &url, bearer, &content_type, body)
            .await?
    } else {
        let part = reqwest::multipart::Part::bytes(torrent_bytes.to_vec())
            .file_name(filename)
            .mime_str("application/x-bittorrent")
            .map_err(|e| ProviderError::Other(format!("Debrid-Link: mime error: {e}")))?;
        let form = reqwest::multipart::Form::new().part("file", part);
        http.post(&url)
            .bearer_auth(bearer)
            .multipart(form)
            .send()
            .await?
    };
    let body: Value = response_json(resp, "dl_post_multipart").await?;
    check_dl_error(&body)?;
    Ok(body)
}

async fn dl_delete(
    http: &reqwest::Client,
    bearer: &str,
    path: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    let url = format!("{BASE_URL}{path}");
    if let Some(fwd) = forward {
        // DELETE via forward — use a dedicated method
        http.delete(fwd.forward_url())
            .query(&[("d", &url), ("api_password", &fwd.api_password)])
            .query(&[("h_authorization", format!("Bearer {bearer}"))])
            .send()
            .await?;
    } else {
        http.delete(&url).bearer_auth(bearer).send().await?;
    }
    Ok(())
}

// ─── Seedbox operations ───────────────────────────────────────────────────────

/// Page through /seedbox/list looking for a torrent whose `hashString` matches
/// `info_hash` (case-insensitive). Returns the torrent object if found.
async fn find_torrent_by_hash(
    http: &reqwest::Client,
    bearer: &str,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<Value>, ProviderError> {
    let mut page = 0usize;
    let per_page = 25usize;

    loop {
        let body = dl_get(
            http,
            bearer,
            "/seedbox/list",
            &[
                ("page", &page.to_string()),
                ("perPage", &per_page.to_string()),
            ],
            forward,
        )
        .await?;

        let items = body
            .get("value")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        if items.is_empty() {
            break;
        }

        for item in &items {
            let hash = item
                .get("hashString")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if hash.to_lowercase() == info_hash.to_lowercase() {
                return Ok(Some(item.clone()));
            }
        }

        if items.len() < per_page {
            break;
        }
        page += 1;
    }

    Ok(None)
}

/// Add a magnet link to the seedbox (async mode). Returns the new torrent object.
async fn add_torrent(
    http: &reqwest::Client,
    bearer: &str,
    magnet: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let body = dl_post(
        http,
        bearer,
        "/seedbox/add",
        json!({ "url": magnet, "async": true }),
        forward,
    )
    .await?;

    body.get("value").cloned().ok_or_else(|| {
        ProviderError::api(
            "No torrent info in Debrid-Link add response",
            "transfer_error.mp4",
        )
    })
}

async fn add_torrent_file(
    http: &reqwest::Client,
    bearer: &str,
    torrent_bytes: &[u8],
    torrent_name: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let body = dl_post_multipart(
        http,
        bearer,
        "/seedbox/add",
        torrent_bytes,
        torrent_name,
        forward,
    )
    .await?;
    body.get("value").cloned().ok_or_else(|| {
        ProviderError::api(
            "No torrent info in Debrid-Link file upload response",
            "transfer_error.mp4",
        )
    })
}

/// Poll until the torrent identified by `torrent_id` has `downloadPercent == 100`.
/// Returns the updated torrent object.
async fn wait_for_download(
    http: &reqwest::Client,
    bearer: &str,
    torrent_id: &str,
    max_retries: u32,
    retry_secs: u64,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    for attempt in 0..max_retries {
        let body = dl_get(
            http,
            bearer,
            "/seedbox/list",
            &[("ids", torrent_id), ("page", "0"), ("perPage", "1")],
            forward,
        )
        .await?;

        let items = body
            .get("value")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        if let Some(torrent) = items.into_iter().next() {
            // Check for errors
            if let Some(err) = torrent.get("errorString").and_then(|v| v.as_str())
                && !err.is_empty() {
                    // Delete the broken torrent and bail
                    dl_delete(
                        http,
                        bearer,
                        &format!("/seedbox/{torrent_id}/delete"),
                        forward,
                    )
                    .await
                    .ok();
                    return Err(ProviderError::api(
                        format!("Debrid-Link torrent error: {err}"),
                        "transfer_error.mp4",
                    ));
                }

            let pct = torrent
                .get("downloadPercent")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            if pct == 100 {
                return Ok(torrent);
            }
        }

        if attempt + 1 < max_retries {
            tokio::time::sleep(tokio::time::Duration::from_secs(retry_secs)).await;
        }
    }
    Err(ProviderError::api(
        format!("Debrid-Link torrent did not finish downloading after {max_retries} retries"),
        "torrent_not_downloaded.mp4",
    ))
}

// ─── File selection helper ────────────────────────────────────────────────────

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

// ─── Public entry points ──────────────────────────────────────────────────────

/// Resolve a direct video URL from Debrid-Link for the given torrent.
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
    const MAX_RETRIES: u32 = 5;
    const RETRY_SECS: u64 = 5;

    let bearer = resolve_bearer(http, token).await?;

    // Build magnet
    let trackers: String = announce_list
        .iter()
        .map(|t| format!("&tr={}", urlencoding::encode(t)))
        .collect();
    let magnet = format!("magnet:?xt=urn:btih:{info_hash}{trackers}");

    // Find or add torrent
    let torrent = match find_torrent_by_hash(http, &bearer, info_hash, forward).await? {
        Some(existing) => {
            // Check if it errored
            let err_str = existing
                .get("errorString")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if !err_str.is_empty() {
                if let Some(id) = existing.get("id").and_then(|v| v.as_str()) {
                    dl_delete(http, &bearer, &format!("/seedbox/{id}/delete"), forward)
                        .await
                        .ok();
                }
                if let Some(bytes) = torrent_file.filter(|b| !b.is_empty()) {
                    add_torrent_file(http, &bearer, bytes, torrent_name, forward).await?
                } else {
                    add_torrent(http, &bearer, &magnet, forward).await?
                }
            } else {
                existing
            }
        }
        None => {
            if let Some(bytes) = torrent_file.filter(|b| !b.is_empty()) {
                add_torrent_file(http, &bearer, bytes, torrent_name, forward).await?
            } else {
                add_torrent(http, &bearer, &magnet, forward).await?
            }
        }
    };

    let torrent_id = torrent
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ProviderError::api("No torrent id from Debrid-Link", "transfer_error.mp4"))?
        .to_string();

    // Wait for download to complete
    let download_pct = torrent
        .get("downloadPercent")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let torrent = if download_pct < 100 {
        wait_for_download(http, &bearer, &torrent_id, MAX_RETRIES, RETRY_SECS, forward).await?
    } else {
        torrent
    };

    // Select file
    let files_arr = torrent
        .get("files")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    // Only consider files that are fully downloaded
    let ready_files: Vec<&Value> = files_arr
        .iter()
        .filter(|f| {
            f.get("downloadPercent")
                .and_then(|v| v.as_i64())
                .unwrap_or(0)
                == 100
        })
        .collect();

    if ready_files.is_empty() {
        return Err(ProviderError::api(
            "No ready files in Debrid-Link torrent",
            "torrent_not_downloaded.mp4",
        ));
    }

    let pairs: Vec<(String, i64)> = ready_files
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

    let release_name = torrent
        .get("name")
        .and_then(|v| v.as_str())
        .or(torrent_name)
        .unwrap_or("");

    let idx = select_video_file(&pairs, release_name, filename, file_index, season, episode);

    let selected = ready_files.get(idx).ok_or_else(|| {
        ProviderError::api(
            "File index out of range for Debrid-Link torrent",
            "torrent_not_downloaded.mp4",
        )
    })?;

    let mut url = selected
        .get("downloadUrl")
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            ProviderError::api(
                "No downloadUrl on Debrid-Link file",
                "torrent_not_downloaded.mp4",
            )
        })?
        .to_string();

    // Append ip= to CDN URL: if forward is set, get MediaFlow's actual public IP;
    // otherwise use the user_ip hint passed by the caller.
    let ip_to_append = if let Some(fwd) = forward {
        Some(fwd.get_public_ip(http).await?)
    } else {
        user_ip.map(str::to_string)
    };
    if let Some(ip) = ip_to_append {
        let sep = if url.contains('?') { '&' } else { '?' };
        url = format!("{url}{sep}ip={}", urlencoding::encode(&ip));
    }

    Ok(url)
}

/// Delete the seedbox torrent matching `info_hash` from the Debrid-Link account.
/// Returns `true` if found and deleted, `false` if not found.
/// Return all fully-downloaded seedbox torrents with their files.
pub async fn list_downloaded_torrents(
    http: &reqwest::Client,
    token: &str,
) -> Result<Vec<crate::providers::torrents::realdebrid::DownloadedTorrent>, ProviderError> {
    let bearer = resolve_bearer(http, token).await?;
    let mut page = 0usize;
    let per_page = 50usize;
    let mut result = Vec::new();

    loop {
        let body = dl_get(
            http,
            &bearer,
            "/seedbox/list",
            &[
                ("page", &page.to_string()),
                ("perPage", &per_page.to_string()),
            ],
            None,
        )
        .await?;

        let items = body
            .get("value")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        if items.is_empty() {
            break;
        }

        for item in &items {
            let pct = item
                .get("downloadPercent")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            if pct < 100 {
                continue;
            }
            if let Some(hash) = item.get("hashString").and_then(|v| v.as_str()) {
                let id = item
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let name = item
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or(hash)
                    .to_string();
                let size = item.get("totalSize").and_then(|v| v.as_i64()).unwrap_or(0);
                let raw = item.clone();
                result.push(crate::providers::torrents::realdebrid::DownloadedTorrent {
                    id,
                    info_hash: hash.to_lowercase(),
                    name,
                    size,
                    raw,
                });
            }
        }

        if items.len() < per_page {
            break;
        }
        page += 1;
    }

    Ok(result)
}

pub async fn delete_torrent_by_hash(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
) -> Result<bool, ProviderError> {
    let bearer = resolve_bearer(http, token).await?;
    match find_torrent_by_hash(http, &bearer, info_hash, None).await? {
        None => Ok(false),
        Some(torrent) => {
            if let Some(id) = torrent.get("id").and_then(|v| v.as_str()) {
                dl_delete(http, &bearer, &format!("/seedbox/{id}/delete"), None).await?;
            }
            Ok(true)
        }
    }
}

/// Delete ALL seedbox torrents from the Debrid-Link account.
pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    let bearer = resolve_bearer(http, token).await?;
    let mut page = 0usize;
    let per_page = 25usize;

    loop {
        let body = dl_get(
            http,
            &bearer,
            "/seedbox/list",
            &[
                ("page", &page.to_string()),
                ("perPage", &per_page.to_string()),
            ],
            None,
        )
        .await?;

        let items = body
            .get("value")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        if items.is_empty() {
            break;
        }

        for item in &items {
            if let Some(id) = item.get("id").and_then(|v| v.as_str()) {
                dl_delete(http, &bearer, &format!("/seedbox/{id}/delete"), None)
                    .await
                    .ok();
            }
        }

        if items.len() < per_page {
            break;
        }
        page += 1;
    }

    Ok(())
}

// ─── Debrid cache check ───────────────────────────────────────────────────────

/// Check which hashes are downloaded in the user's DebridLink account.
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    use std::collections::HashSet;
    const PER_PAGE: usize = 25;
    const MAX_PAGES: usize = 100;

    let bearer = match decode_token(token) {
        TokenKind::Private(t) => t,
        TokenKind::Refresh(refresh) => match exchange_refresh_token(http, &refresh).await {
            Ok(t) => t,
            Err(_) => return vec![],
        },
    };

    let hash_set: HashSet<String> = hashes.iter().map(|h| h.to_lowercase()).collect();
    let mut found = Vec::new();

    for page in 0..MAX_PAGES {
        let per_page_str = PER_PAGE.to_string();
        let page_str = page.to_string();
        let resp = match http
            .get(format!("{BASE_URL}/seedbox/list"))
            .bearer_auth(&bearer)
            .query(&[
                ("page", page_str.as_str()),
                ("perPage", per_page_str.as_str()),
            ])
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!("debridlink seedbox/list page {page}: {e}");
                break;
            }
        };
        let body: serde_json::Value =
            match response_json(resp, &format!("debridlink seedbox/list page {page}")).await {
                Ok(v) => v,
                Err(_) => break,
            };
        let arr = match body.get("value").and_then(|v| v.as_array()) {
            Some(a) if !a.is_empty() => a.clone(),
            _ => break,
        };
        for t in &arr {
            if t.get("downloadPercent").and_then(|v| v.as_i64()) == Some(100)
                && let Some(h) = t.get("hashString").and_then(|v| v.as_str()) {
                    let lower = h.to_lowercase();
                    if hash_set.contains(&lower) {
                        found.push(lower);
                    }
                }
        }
        if (page == 0 && arr.len() > PER_PAGE) || arr.len() < PER_PAGE {
            break;
        }
    }
    found
}
