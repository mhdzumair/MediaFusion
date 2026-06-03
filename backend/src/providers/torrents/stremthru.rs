/// StremThru streaming provider.
///
/// Token formats:
///   - `{store_name}:{store_token}` → store-level auth headers
///   - plain base64 string          → `Proxy-Authorization: Basic {token}`
///
/// The base URL may be prepended to the token as `{url}|{auth}`, otherwise
/// `https://stremthru.432hz.dev` is used.
use serde_json::Value;

use crate::providers::{file_selection::select_debrid_file_index, response_json, ProviderError};

const DEFAULT_BASE_URL: &str = "https://stremthru.432hz.dev";
const USER_AGENT: &str = "mediafusion";

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

// ─── Token / config parsing ───────────────────────────────────────────────────

struct StremThruConfig {
    base_url: String,
    auth: StremThruAuth,
}

enum StremThruAuth {
    /// Store-level auth: X-StremThru-Store-Name + X-StremThru-Store-Authorization
    Store {
        store_name: String,
        store_token: String,
    },
    /// Proxy auth: Proxy-Authorization: Basic {token}
    Proxy(String),
}

fn parse_config(token: &str) -> StremThruConfig {
    // Optionally extract base URL prefix: "{url}|{auth}"
    let (base_url, auth_str) = if let Some(pipe_pos) = token.find('|') {
        let url = token[..pipe_pos].to_string();
        let rest = &token[pipe_pos + 1..];
        (url, rest.to_string())
    } else {
        (DEFAULT_BASE_URL.to_string(), token.to_string())
    };

    // If auth_str contains ":", it is "{store_name}:{store_token}"
    let auth = if let Some(colon_pos) = auth_str.find(':') {
        let store_name = auth_str[..colon_pos].to_string();
        let store_token = auth_str[colon_pos + 1..].to_string();
        StremThruAuth::Store {
            store_name,
            store_token,
        }
    } else {
        StremThruAuth::Proxy(auth_str)
    };

    StremThruConfig { base_url, auth }
}

// ─── Error mapping ────────────────────────────────────────────────────────────

fn check_stremthru_error(body: &Value) -> Result<(), ProviderError> {
    if let Some(err) = body.get("error") {
        let code = err.get("code").and_then(|v| v.as_str()).unwrap_or("");
        let (msg, file) = match code {
            "FORBIDDEN" | "UNAUTHORIZED" => {
                ("Invalid Token / Permission Denied", "invalid_token.mp4")
            }
            "PAYMENT_REQUIRED" => ("Need to upgrade plan", "need_premium.mp4"),
            "TOO_MANY_REQUESTS" => ("Too many requests", "too_many_requests.mp4"),
            _ => {
                let detail = err
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Unknown StremThru error");
                return Err(ProviderError::api(
                    format!("StremThru error {code}: {detail}"),
                    "api_error.mp4",
                ));
            }
        };
        return Err(ProviderError::api(msg, file));
    }
    Ok(())
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

fn apply_auth(builder: reqwest::RequestBuilder, auth: &StremThruAuth) -> reqwest::RequestBuilder {
    match auth {
        StremThruAuth::Store {
            store_name,
            store_token,
        } => builder.header("X-StremThru-Store-Name", store_name).header(
            "X-StremThru-Store-Authorization",
            format!("Bearer {store_token}"),
        ),
        StremThruAuth::Proxy(token) => {
            builder.header("X-StremThru-Authorization", format!("Basic {token}"))
        }
    }
}

async fn st_get(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    path: &str,
) -> Result<Value, ProviderError> {
    let url = format!("{}{path}", cfg.base_url);
    let req = http.get(&url).header("User-Agent", USER_AGENT);
    let req = apply_auth(req, &cfg.auth);
    let resp = req.send().await?;
    let body: Value = response_json(resp, "st_get").await?;
    check_stremthru_error(&body)?;
    Ok(body)
}

async fn st_post(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    path: &str,
    payload: &Value,
) -> Result<Value, ProviderError> {
    let url = format!("{}{path}", cfg.base_url);
    let req = http
        .post(&url)
        .header("User-Agent", USER_AGENT)
        .json(payload);
    let req = apply_auth(req, &cfg.auth);
    let resp = req.send().await?;
    let body: Value = response_json(resp, "st_post").await?;
    check_stremthru_error(&body)?;
    Ok(body)
}

async fn st_delete(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    path: &str,
) -> Result<(), ProviderError> {
    let url = format!("{}{path}", cfg.base_url);
    let req = http.delete(&url).header("User-Agent", USER_AGENT);
    let req = apply_auth(req, &cfg.auth);
    req.send().await?;
    Ok(())
}

// ─── Public validation ────────────────────────────────────────────────────────

/// Validate StremThru credentials by fetching the store user info. Returns Ok(()) on success.
pub async fn validate_credentials(
    http: &reqwest::Client,
    token: &str,
) -> Result<(), ProviderError> {
    let cfg = parse_config(token);
    st_get(http, &cfg, "/v0/store/user").await?;
    Ok(())
}

// ─── StremThru API operations ─────────────────────────────────────────────────

async fn add_torz(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    magnet: &str,
) -> Result<Value, ProviderError> {
    let payload = serde_json::json!({ "link": magnet });
    st_post(http, cfg, "/v0/store/torz", &payload).await
}

async fn get_torz_status(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    torz_id: &str,
) -> Result<Value, ProviderError> {
    st_get(http, cfg, &format!("/v0/store/torz/{torz_id}")).await
}

async fn generate_link(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    file_link: &str,
) -> Result<String, ProviderError> {
    let payload = serde_json::json!({ "link": file_link });
    let body = st_post(http, cfg, "/v0/store/torz/link/generate", &payload).await?;
    body.get("data")
        .and_then(|d| d.get("link"))
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| {
            ProviderError::api(
                "Missing link in StremThru generate response",
                "api_error.mp4",
            )
        })
}

async fn list_torz(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
) -> Result<Vec<Value>, ProviderError> {
    let body = st_get(http, cfg, "/v0/store/torz").await?;
    Ok(body
        .get("data")
        .and_then(|d| d.get("items"))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default())
}

// ─── Wait for magnet to be downloaded ────────────────────────────────────────

async fn wait_for_downloaded(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    torz_id: &str,
    max_retries: u32,
    retry_interval_secs: u64,
) -> Result<Value, ProviderError> {
    for attempt in 0..max_retries {
        let info = get_torz_status(http, cfg, torz_id).await?;
        let status = info
            .get("data")
            .and_then(|d| d.get("status"))
            .and_then(|v| v.as_str())
            .unwrap_or("");

        if status.eq_ignore_ascii_case("downloaded") {
            return Ok(info);
        }

        if matches!(status, "failed" | "invalid") {
            return Err(ProviderError::api(
                format!("StremThru torz entered terminal status: {status}"),
                "transfer_error.mp4",
            ));
        }

        if attempt + 1 < max_retries {
            tokio::time::sleep(tokio::time::Duration::from_secs(retry_interval_secs)).await;
        }
    }
    Err(ProviderError::api(
        format!("StremThru torz did not reach 'downloaded' status after {max_retries} retries"),
        "torrent_not_downloaded.mp4",
    ))
}

// ─── Public entry points ──────────────────────────────────────────────────────

/// Return all torz stored in the user's StremThru account.
pub async fn list_downloaded_torrents(
    http: &reqwest::Client,
    token: &str,
) -> Result<Vec<crate::providers::torrents::realdebrid::DownloadedTorrent>, ProviderError> {
    let cfg = parse_config(token);
    let items = list_torz(http, &cfg).await?;
    let mut results = Vec::with_capacity(items.len());
    for item in items {
        let info_hash = match item.get("hash").and_then(|v| v.as_str()) {
            Some(h) => h.to_lowercase(),
            None => continue,
        };
        let id = item
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let name = item
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(&info_hash)
            .to_string();
        let size = item.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
        results.push(crate::providers::torrents::realdebrid::DownloadedTorrent {
            id,
            info_hash,
            name,
            size,
            raw: item,
        });
    }
    Ok(results)
}

/// Resolve a direct video URL from StremThru for the given torrent.
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
    _forward: Option<&crate::providers::torrents::transport::MediaFlowForward>,
) -> Result<String, ProviderError> {
    const MAX_RETRIES: u32 = 5;
    const RETRY_INTERVAL: u64 = 5;

    let cfg = parse_config(token);

    let magnet = format!(
        "magnet:?xt=urn:btih:{}&{}",
        info_hash,
        announce_list
            .iter()
            .map(|t| format!("tr={}", urlencoding::encode(t)))
            .collect::<Vec<_>>()
            .join("&")
    );

    // Add torz — StremThru returns the torrent data directly (or existing one)
    let add_resp = add_torz(http, &cfg, &magnet).await?;

    let data = add_resp.get("data").ok_or_else(|| {
        ProviderError::api(
            "Missing data in StremThru add torz response",
            "api_error.mp4",
        )
    })?;

    let torz_id = data
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            ProviderError::api("Missing torz id in StremThru response", "api_error.mp4")
        })?
        .to_string();

    // Check status — may already be downloaded
    let status = data.get("status").and_then(|v| v.as_str()).unwrap_or("");

    let torrent_data = if status.eq_ignore_ascii_case("downloaded") {
        add_resp.clone()
    } else {
        wait_for_downloaded(http, &cfg, &torz_id, MAX_RETRIES, RETRY_INTERVAL).await?
    };

    // Extract files list
    let files_raw: Vec<Value> = torrent_data
        .get("data")
        .and_then(|d| d.get("files"))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    if files_raw.is_empty() {
        return Err(ProviderError::api(
            "No files found in StremThru torrent",
            "torrent_not_downloaded.mp4",
        ));
    }

    let name_size: Vec<(String, i64)> = files_raw
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

    let release_name = torrent_data
        .get("data")
        .and_then(|d| d.get("name"))
        .and_then(|v| v.as_str())
        .unwrap_or("");

    let idx = select_video_file(
        &name_size,
        release_name,
        filename,
        file_index,
        season,
        episode,
    );

    let file_link = files_raw[idx]
        .get("link")
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            ProviderError::api(
                "Selected file has no link in StremThru response",
                "api_error.mp4",
            )
        })?;

    generate_link(http, &cfg, file_link).await
}

/// Delete ALL torz from the user's StremThru store.
pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    let cfg = parse_config(token);
    let items = list_torz(http, &cfg).await?;

    for item in items {
        if let Some(id) = item.get("id").and_then(|v| v.as_str()) {
            st_delete(http, &cfg, &format!("/v0/store/torz/{id}"))
                .await
                .ok();
        }
    }

    Ok(())
}

// ─── Debrid cache check ───────────────────────────────────────────────────────

/// Check which hashes are cached in the user's StremThru store.
pub async fn check_cached(
    http: &reqwest::Client,
    token: &str,
    hashes: &[String],
    media_id: i32,
) -> Vec<String> {
    // Send hashes in chunks to avoid oversized GET URLs (HTTP 414).
    const CHUNK: usize = 80;
    let cfg = parse_config(token);
    let url = format!("{}/v0/store/torz/check", cfg.base_url);
    let sid = media_id.to_string();
    let mut cached = Vec::new();

    for chunk in hashes.chunks(CHUNK) {
        let hash_list = chunk.join(",");
        let req = http
            .get(&url)
            .header("User-Agent", USER_AGENT)
            .query(&[("hash", hash_list.as_str()), ("sid", sid.as_str())]);
        let req = apply_auth(req, &cfg.auth);
        let resp = match req.send().await {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!("stremthru torz/check: {e}");
                continue;
            }
        };
        let body: serde_json::Value = match response_json(resp, "stremthru torz/check").await {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(items) = body
            .get("data")
            .and_then(|d| d.get("items"))
            .and_then(|v| v.as_array())
        {
            for item in items {
                if item.get("status").and_then(|v| v.as_str()) == Some("cached") {
                    if let Some(h) = item.get("hash").and_then(|v| v.as_str()) {
                        cached.push(h.to_string());
                    }
                }
            }
        }
    }

    cached
}
