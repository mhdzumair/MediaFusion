/// StremThru streaming provider.
///
/// Token formats:
///   - `{store_name}:{store_token}` → store-level auth headers
///   - plain base64 string          → `Proxy-Authorization: Basic {token}`
///
/// The base URL may be prepended to the token as `{url}|{auth}`, otherwise
/// `https://stremthru.432hz.dev` is used.
use serde_json::Value;
use std::sync::OnceLock;

use crate::providers::ProviderError;

const DEFAULT_BASE_URL: &str = "https://stremthru.432hz.dev";
const USER_AGENT: &str = "mediafusion";

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
            builder.header("Proxy-Authorization", format!("Basic {token}"))
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
    let body: Value = resp.json().await?;
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
    let body: Value = resp.json().await?;
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

// ─── StremThru API operations ─────────────────────────────────────────────────

async fn add_magnet(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    magnet: &str,
) -> Result<Value, ProviderError> {
    let payload = serde_json::json!({ "magnet": magnet });
    st_post(http, cfg, "/v0/store/magnets", &payload).await
}

async fn get_magnet_status(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    magnet_id: &str,
) -> Result<Value, ProviderError> {
    st_get(http, cfg, &format!("/v0/store/magnets/{magnet_id}")).await
}

async fn generate_link(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
    file_link: &str,
) -> Result<String, ProviderError> {
    let payload = serde_json::json!({ "link": file_link });
    let body = st_post(http, cfg, "/v0/store/link/generate", &payload).await?;
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

async fn list_magnets(
    http: &reqwest::Client,
    cfg: &StremThruConfig,
) -> Result<Vec<Value>, ProviderError> {
    let body = st_get(http, cfg, "/v0/store/magnets").await?;
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
    magnet_id: &str,
    max_retries: u32,
    retry_interval_secs: u64,
) -> Result<Value, ProviderError> {
    for attempt in 0..max_retries {
        let info = get_magnet_status(http, cfg, magnet_id).await?;
        let status = info
            .get("data")
            .and_then(|d| d.get("status"))
            .and_then(|v| v.as_str())
            .unwrap_or("");

        if status.eq_ignore_ascii_case("downloaded") {
            return Ok(info);
        }

        // Terminal error states
        if matches!(status, "error" | "failed") {
            return Err(ProviderError::api(
                format!("StremThru magnet entered error status: {status}"),
                "transfer_error.mp4",
            ));
        }

        if attempt + 1 < max_retries {
            tokio::time::sleep(tokio::time::Duration::from_secs(retry_interval_secs)).await;
        }
    }
    Err(ProviderError::api(
        format!("StremThru magnet did not reach 'downloaded' status after {max_retries} retries"),
        "torrent_not_downloaded.mp4",
    ))
}

// ─── Public entry points ──────────────────────────────────────────────────────

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

    // Add magnet — StremThru returns the torrent data directly (or existing one)
    let add_resp = add_magnet(http, &cfg, &magnet).await?;

    let data = add_resp.get("data").ok_or_else(|| {
        ProviderError::api(
            "Missing data in StremThru add magnet response",
            "api_error.mp4",
        )
    })?;

    let magnet_id = data
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            ProviderError::api("Missing magnet id in StremThru response", "api_error.mp4")
        })?
        .to_string();

    // Check status — may already be downloaded
    let status = data.get("status").and_then(|v| v.as_str()).unwrap_or("");

    let torrent_data = if status.eq_ignore_ascii_case("downloaded") {
        add_resp.clone()
    } else {
        wait_for_downloaded(http, &cfg, &magnet_id, MAX_RETRIES, RETRY_INTERVAL).await?
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

    let idx = select_video_file(&name_size, filename, file_index, season, episode);

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

/// Delete ALL magnets from the user's StremThru store.
pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    let cfg = parse_config(token);
    let items = list_magnets(http, &cfg).await?;

    for item in items {
        if let Some(id) = item.get("id").and_then(|v| v.as_str()) {
            st_delete(http, &cfg, &format!("/v0/store/magnets/{id}"))
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
    let cfg = parse_config(token);
    let magnet_list = hashes.join(",");
    let url = format!("{}/v0/store/magnets/check", cfg.base_url);
    let req = http.get(&url).header("User-Agent", USER_AGENT).query(&[
        ("magnet", magnet_list.as_str()),
        ("sid", &media_id.to_string()),
    ]);
    let req = apply_auth(req, &cfg.auth);
    let body: serde_json::Value = match req.send().await {
        Ok(r) => match r.json().await {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!("stremthru magnets/check json: {e}");
                return vec![];
            }
        },
        Err(e) => {
            tracing::warn!("stremthru magnets/check: {e}");
            return vec![];
        }
    };
    body.get("data")
        .and_then(|d| d.get("items"))
        .and_then(|v| v.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    if item.get("status").and_then(|v| v.as_str()) == Some("cached") {
                        item.get("hash")
                            .and_then(|v| v.as_str())
                            .map(str::to_string)
                    } else {
                        None
                    }
                })
                .collect()
        })
        .unwrap_or_default()
}
