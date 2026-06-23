/// PikPak streaming provider.
///
/// Token format: base64-encoded JSON:
/// `{"access_token":"...","refresh_token":"...","device_id":"...","user_id":"...","captcha_token":"..."}`
///
/// Auth follows the PikPak web client (rclone / debrify):
///   - Web client_id + captcha tokens on every API call
///   - Token refresh WITHOUT client_secret (sending it triggers permission_denied)
///
/// API hosts:
///   Drive: api-drive.mypikpak.com
///   User:  user.mypikpak.com
use base64::{
    Engine,
    engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
};
use reqwest::Client;
use serde_json::{Value, json};

use crate::providers::{
    ProviderError,
    torrents::transport::{MediaFlowForward, append_query},
};

const API_HOST: &str = "api-drive.mypikpak.com";
const USER_HOST: &str = "user.mypikpak.com";
const CLIENT_ID: &str = "YUMx5nI8ZU8Ap8pm";
const CLIENT_SECRET: &str = "dbw2OtmVEeuUvIptb1Coygx";
const CLIENT_VERSION: &str = "2.0.0";
const PACKAGE_NAME: &str = "mypikpak.com";
const USER_AGENT: &str =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0";
const REDIRECT_URI: &str = "xlaccsdk01://xbase.cloud/callback?state=harbor";

/// Web-platform captcha sign salts (rclone / debrify).
const CAPTCHA_SALTS: &[&str] = &[
    "C9qPpZLN8ucRTaTiUMWYS9cQvWOE",
    "+r6CQVxjzJV6LCV",
    "F",
    "pFJRC",
    "9WXYIDGrwTCz2OiVlgZa90qpECPD6olt",
    "/750aCr4lm/Sly/c",
    "RB+DT/gZCrbV",
    "",
    "CyLsf7hdkIRxRm215hl",
    "7xHvLi2tOYP0Y92b",
    "ZGTXXxu8E/MIWaEDB+Sm/",
    "1UI3",
    "E7fP5Pfijd+7K+t6Tg/NhuLq0eEUVChpJSkrKxpO",
    "ihtqpG6FMt65+Xk+tWUH2",
    "NhXXU9rg4XXdzo7u5o",
];

const MAX_RETRIES: u32 = 3;
const RETRY_SECS: u64 = 5;

static VIDEO_EXTS: &[&str] = &["mkv", "mp4", "avi", "webm", "mov", "flv", "m4v", "wmv"];

// ─── Token / session ──────────────────────────────────────────────────────────

struct Tokens {
    access_token: String,
    refresh_token: String,
    device_id: String,
    user_id: String,
    captcha_token: Option<String>,
    /// Cached from `/drive/v1/about` for the current playback session.
    is_premium: Option<bool>,
}

fn encode_token(tokens: &Tokens) -> String {
    let mut obj = json!({
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "device_id": tokens.device_id,
    });
    if !tokens.user_id.is_empty() {
        obj["user_id"] = json!(tokens.user_id);
    }
    if let Some(ref ct) = tokens.captcha_token {
        obj["captcha_token"] = json!(ct);
    }
    STANDARD.encode(serde_json::to_string(&obj).unwrap_or_default().as_bytes())
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
    if is_legacy_token_value(&v) {
        return Err(ProviderError::api(
            "PikPak session uses an outdated token format. Please reconnect your PikPak account.",
            "invalid_token.mp4",
        ));
    }
    let device_id = v["device_id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .expect("legacy tokens are rejected above");
    let user_id = v["user_id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| extract_user_id_from_jwt(&access));
    let captcha_token = v["captcha_token"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    Ok(Tokens {
        access_token: access,
        refresh_token: refresh,
        device_id,
        user_id,
        captcha_token,
        is_premium: None,
    })
}

/// Stable device ID derived from credentials (persisted across logins).
fn pikpak_device_id(email: &str, password: &str) -> String {
    format!(
        "{:x}",
        md5::compute(format!("{email}{password}").as_bytes())
    )
}

fn is_legacy_token_value(v: &Value) -> bool {
    v.get("device_id")
        .and_then(|d| d.as_str())
        .is_none_or(|s| s.is_empty())
}

/// Returns true when the stored token predates the web-client session format (no `device_id`).
pub fn is_legacy_token(raw: &str) -> bool {
    let Ok(decoded) = STANDARD.decode(raw.trim()) else {
        return true;
    };
    let Ok(s) = String::from_utf8(decoded) else {
        return true;
    };
    let Ok(v) = serde_json::from_str::<Value>(&s) else {
        return true;
    };
    is_legacy_token_value(&v)
}

/// Stable cache key component for a given email+password pair.
pub fn token_cache_id(email: &str, password: &str) -> String {
    pikpak_device_id(email, password)
}

fn extract_user_id_from_jwt(access_token: &str) -> String {
    let payload = match access_token.split('.').nth(1) {
        Some(p) => p,
        None => return String::new(),
    };
    URL_SAFE_NO_PAD
        .decode(payload)
        .ok()
        .and_then(|b| String::from_utf8(b).ok())
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .and_then(|v| v["sub"].as_str().map(str::to_string))
        .unwrap_or_default()
}

// ─── Captcha ──────────────────────────────────────────────────────────────────

fn compute_captcha_sign(device_id: &str, timestamp: &str) -> String {
    let mut sign = format!("{CLIENT_ID}{CLIENT_VERSION}{PACKAGE_NAME}{device_id}{timestamp}");
    for salt in CAPTCHA_SALTS {
        sign = format!("{:x}", md5::compute(format!("{sign}{salt}").as_bytes()));
    }
    format!("1.{sign}")
}

fn build_auth_headers(device_id: &str, captcha_token: Option<&str>) -> reqwest::header::HeaderMap {
    let mut h = reqwest::header::HeaderMap::new();
    h.insert(
        reqwest::header::CONTENT_TYPE,
        "application/json".parse().unwrap(),
    );
    h.insert(reqwest::header::USER_AGENT, USER_AGENT.parse().unwrap());
    if let Ok(val) = CLIENT_ID.parse() {
        h.insert("X-Client-ID", val);
    }
    if let Ok(val) = CLIENT_VERSION.parse() {
        h.insert("X-Client-Version", val);
    }
    if let Ok(val) = device_id.parse() {
        h.insert("X-Device-ID", val);
    }
    if let Some(ct) = captcha_token.filter(|s| !s.is_empty()) {
        if let Ok(val) = ct.parse() {
            h.insert("X-Captcha-Token", val);
        }
    }
    h
}

fn build_api_headers(tokens: &Tokens) -> reqwest::header::HeaderMap {
    let mut h = build_auth_headers(&tokens.device_id, tokens.captcha_token.as_deref());
    h.insert(
        reqwest::header::AUTHORIZATION,
        format!("Bearer {}", tokens.access_token).parse().unwrap(),
    );
    h
}

/// Request a captcha token for the given API action (e.g. `POST:/v1/auth/signin`).
async fn request_captcha_token(
    http: &Client,
    action: &str,
    device_id: &str,
    username: Option<&str>,
    user_id: Option<&str>,
    old_captcha_token: Option<&str>,
    access_token: Option<&str>,
) -> Result<String, ProviderError> {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .to_string();
    let captcha_sign = compute_captcha_sign(device_id, &timestamp);

    let mut meta = json!({
        "captcha_sign": captcha_sign,
        "client_id": CLIENT_ID,
        "client_version": CLIENT_VERSION,
        "device_id": device_id,
        "package_name": PACKAGE_NAME,
        "timestamp": timestamp,
    });
    if action == "POST:/v1/auth/signin" {
        if let Some(email) = username {
            meta["username"] = json!(email);
        }
    } else if let Some(uid) = user_id.filter(|s| !s.is_empty()) {
        meta["user_id"] = json!(uid);
    }

    let url = format!(
        "{}/v1/shield/captcha/init?client_id={CLIENT_ID}",
        user_base()
    );
    let body = json!({
        "action": action,
        "captcha_token": old_captcha_token.unwrap_or(""),
        "client_id": CLIENT_ID,
        "device_id": device_id,
        "meta": meta,
        "redirect_uri": REDIRECT_URI,
    });

    let mut headers = build_auth_headers(device_id, None);
    if let Some(token) = access_token {
        headers.insert(
            reqwest::header::AUTHORIZATION,
            format!("Bearer {token}").parse().unwrap(),
        );
    }

    let data: Value = http
        .post(&url)
        .headers(headers)
        .json(&body)
        .send()
        .await?
        .json()
        .await
        .unwrap_or_default();

    data["captcha_token"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            let msg = data["error_description"]
                .as_str()
                .or_else(|| data["error"].as_str())
                .unwrap_or("captcha init failed");
            ProviderError::api(
                format!("PikPak captcha init failed: {msg}"),
                "invalid_credentials.mp4",
            )
        })
}

/// Ensure `tokens.captcha_token` is set for the given drive/user action.
async fn ensure_captcha(
    http: &Client,
    tokens: &mut Tokens,
    action: &str,
) -> Result<(), ProviderError> {
    if tokens.captcha_token.is_some() {
        return Ok(());
    }
    let ct = request_captcha_token(
        http,
        action,
        &tokens.device_id,
        None,
        Some(&tokens.user_id),
        None,
        Some(&tokens.access_token),
    )
    .await?;
    tokens.captcha_token = Some(ct);
    Ok(())
}

fn invalidate_captcha(tokens: &mut Tokens) {
    tokens.captcha_token = None;
}

fn api_error_code(data: &Value) -> Option<i64> {
    data.get("error_code").and_then(|v| v.as_i64())
}

fn is_captcha_error(data: &Value) -> bool {
    api_error_code(data) == Some(4002)
        || data["error"]
            .as_str()
            .is_some_and(|e| e == "captcha_invalid")
}

fn is_auth_error(data: &Value) -> bool {
    api_error_code(data) == Some(16)
        || data["error"]
            .as_str()
            .is_some_and(|e| e == "unauthenticated")
}

// ─── Login / refresh ──────────────────────────────────────────────────────────

/// Authenticate with email+password and return a base64-encoded session token.
pub async fn login(http: &Client, email: &str, password: &str) -> Result<String, ProviderError> {
    let device_id = pikpak_device_id(email, password);

    let captcha_token = request_captcha_token(
        http,
        "POST:/v1/auth/signin",
        &device_id,
        Some(email),
        None,
        None,
        None,
    )
    .await?;

    let url = format!("{}/v1/auth/signin?client_id={CLIENT_ID}", user_base());
    let body = json!({
        "captcha_token": captcha_token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username": email,
        "password": password,
    });

    let headers = build_auth_headers(&device_id, Some(&captcha_token));
    let data: Value = http
        .post(&url)
        .headers(headers.clone())
        .json(&body)
        .send()
        .await?
        .json()
        .await
        .unwrap_or_default();

    if let Some(err) = data.get("error") {
        let msg = data["error_description"]
            .as_str()
            .unwrap_or_else(|| err.as_str().unwrap_or("PikPak login failed"));
        tracing::debug!(email = %email, error = %msg, "PikPak login API error");
        return Err(map_pikpak_error(msg));
    }

    let access = data["access_token"]
        .as_str()
        .ok_or_else(|| {
            ProviderError::api(
                "PikPak login: missing access_token.",
                "invalid_credentials.mp4",
            )
        })?
        .to_string();
    let refresh = data["refresh_token"]
        .as_str()
        .ok_or_else(|| {
            ProviderError::api(
                "PikPak login: missing refresh_token.",
                "invalid_credentials.mp4",
            )
        })?
        .to_string();
    let user_id = data["sub"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| extract_user_id_from_jwt(&access));

    Ok(encode_token(&Tokens {
        access_token: access,
        refresh_token: refresh,
        device_id,
        user_id,
        captcha_token: Some(captcha_token),
        is_premium: None,
    }))
}

/// Refresh access token WITHOUT client_secret (PikPak rejects it with error_code 7).
async fn refresh_tokens(http: &Client, tokens: &mut Tokens) -> Result<(), ProviderError> {
    invalidate_captcha(tokens);
    ensure_captcha(http, tokens, "POST:/v1/auth/token").await?;

    let url = format!("{}/v1/auth/token?client_id={CLIENT_ID}", user_base());
    let body = json!({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": tokens.refresh_token,
    });

    let headers = build_auth_headers(&tokens.device_id, tokens.captcha_token.as_deref());
    let data: Value = http
        .post(&url)
        .headers(headers)
        .json(&body)
        .send()
        .await?
        .json()
        .await
        .unwrap_or_default();

    if data.get("error").is_some() || api_error_code(&data).is_some() {
        let msg = data["error_description"]
            .as_str()
            .or_else(|| data["error"].as_str())
            .unwrap_or("token refresh failed");
        tracing::debug!(error = %msg, "PikPak token refresh failed");
        return Err(ProviderError::api(
            "PikPak token is expired or invalid. Please reconnect your PikPak account.",
            "invalid_token.mp4",
        ));
    }

    tokens.access_token = data["access_token"]
        .as_str()
        .ok_or_else(|| ProviderError::api("PikPak token refresh failed.", "invalid_token.mp4"))?
        .to_string();
    if let Some(rt) = data["refresh_token"].as_str().filter(|s| !s.is_empty()) {
        tokens.refresh_token = rt.to_string();
    }
    if let Some(sub) = data["sub"].as_str().filter(|s| !s.is_empty()) {
        tokens.user_id = sub.to_string();
    } else if tokens.user_id.is_empty() {
        tokens.user_id = extract_user_id_from_jwt(&tokens.access_token);
    }
    Ok(())
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

fn drive_url(path: &str) -> String {
    format!("https://{API_HOST}{path}")
}

fn user_base() -> String {
    format!("https://{USER_HOST}")
}

async fn api_get(
    http: &Client,
    tokens: &mut Tokens,
    path: &str,
    params: &[(&str, &str)],
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let action = format!("GET:{path}");
    api_request(http, tokens, "GET", path, params, None, &action, forward).await
}

async fn api_post(
    http: &Client,
    tokens: &mut Tokens,
    path: &str,
    body: &Value,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let action = format!("POST:{path}");
    api_request(
        http,
        tokens,
        "POST",
        path,
        &[],
        Some(body),
        &action,
        forward,
    )
    .await
}

async fn api_request(
    http: &Client,
    tokens: &mut Tokens,
    method: &str,
    path: &str,
    params: &[(&str, &str)],
    body: Option<&Value>,
    action: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    ensure_captcha(http, tokens, action).await?;
    let data = do_api_request(http, tokens, method, path, params, body, forward).await?;

    if is_auth_error(&data) {
        refresh_tokens(http, tokens).await?;
        ensure_captcha(http, tokens, action).await?;
        let data2 = do_api_request(http, tokens, method, path, params, body, forward).await?;
        return check_api_error(data2);
    }

    if is_captcha_error(&data) {
        invalidate_captcha(tokens);
        ensure_captcha(http, tokens, action).await?;
        let data2 = do_api_request(http, tokens, method, path, params, body, forward).await?;
        return check_api_error(data2);
    }

    check_api_error(data)
}

async fn do_api_request(
    http: &Client,
    tokens: &Tokens,
    method: &str,
    path: &str,
    params: &[(&str, &str)],
    body: Option<&Value>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = drive_url(path);
    let headers = build_api_headers(tokens);

    let data: Value = if method == "GET" {
        if let Some(fwd) = forward {
            let dest = append_query(&url, params);
            fwd.get(http, &dest, &tokens.access_token)
                .await?
                .json()
                .await
                .unwrap_or_default()
        } else {
            http.get(&url)
                .headers(headers)
                .query(params)
                .send()
                .await?
                .json()
                .await
                .unwrap_or_default()
        }
    } else if let Some(b) = body {
        if let Some(fwd) = forward {
            fwd.post_json(http, &url, &tokens.access_token, b.to_string())
                .await?
                .json()
                .await
                .unwrap_or_default()
        } else {
            http.post(&url)
                .headers(headers)
                .json(b)
                .send()
                .await?
                .json()
                .await
                .unwrap_or_default()
        }
    } else {
        return Err(ProviderError::api(
            "PikPak internal API error.",
            "api_error.mp4",
        ));
    };

    Ok(data)
}

fn check_api_error(data: Value) -> Result<Value, ProviderError> {
    if let Some(err) = data.get("error") {
        let msg = data["error_description"]
            .as_str()
            .unwrap_or_else(|| err.as_str().unwrap_or("PikPak API error"));
        return Err(map_pikpak_error(msg));
    }
    if let Some(code) = api_error_code(&data) {
        if code != 0 {
            let msg = data["error_description"]
                .as_str()
                .unwrap_or("PikPak API error");
            return Err(map_pikpak_error(msg));
        }
    }
    Ok(data)
}

// ─── Task helpers (Python parity) ─────────────────────────────────────────────

fn default_file_filters(extra: Option<&Value>) -> String {
    let mut filters = json!({
        "trashed": {"eq": false},
        "phase": {"eq": "PHASE_TYPE_COMPLETE"},
    });
    if let Some(extra_obj) = extra.and_then(|v| v.as_object()) {
        for (k, v) in extra_obj {
            filters[k] = v.clone();
        }
    }
    serde_json::to_string(&filters).unwrap_or_default()
}

fn trashed_only_file_filters() -> String {
    serde_json::to_string(&json!({"trashed": {"eq": false}})).unwrap_or_default()
}

fn parse_pikpak_size(value: &Value) -> i64 {
    value
        .as_str()
        .and_then(|s| s.parse().ok())
        .or_else(|| value.as_i64())
        .unwrap_or(0)
}

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

async fn offline_tasks_for_hash(
    http: &Client,
    tokens: &mut Tokens,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<Value>, ProviderError> {
    let tasks = offline_list(
        http,
        tokens,
        &[
            "PHASE_TYPE_RUNNING",
            "PHASE_TYPE_ERROR",
            "PHASE_TYPE_COMPLETE",
            "PHASE_TYPE_PENDING",
        ],
        forward,
    )
    .await?;

    let mut matched: Vec<Value> = tasks
        .iter()
        .filter(|task| task_has_info_hash(task, info_hash))
        .cloned()
        .collect();

    if matched.is_empty() {
        for task in &tasks {
            if task_phase(task) != "PHASE_TYPE_COMPLETE" {
                continue;
            }
            let Some(file_id) = task_file_id(task) else {
                continue;
            };
            let detail = get_file_detail(http, tokens, &file_id, forward).await?;
            if detail.get("error").is_none() && item_has_info_hash(&detail, info_hash) {
                matched.push(task.clone());
            }
        }
    }

    Ok(matched)
}

async fn check_torrent_status(
    http: &Client,
    tokens: &mut Tokens,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<Value>, ProviderError> {
    let tasks = offline_tasks_for_hash(http, tokens, info_hash, forward).await?;
    // Prefer completed downloads over stale in-progress tasks.
    let priority = |task: &Value| -> u8 {
        match task_phase(task) {
            "PHASE_TYPE_COMPLETE" => 0,
            "PHASE_TYPE_RUNNING" | "PHASE_TYPE_PENDING" => 1,
            "PHASE_TYPE_ERROR" => 2,
            _ => 3,
        }
    };
    Ok(tasks.into_iter().min_by_key(|task| priority(task)))
}

fn task_file_id(task: &Value) -> Option<String> {
    task["file_id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .or_else(|| {
            task["reference_resource"]["id"]
                .as_str()
                .filter(|s| !s.is_empty())
                .map(str::to_string)
        })
}

async fn drive_file_exists(
    http: &Client,
    tokens: &mut Tokens,
    file_id: &str,
    forward: Option<&MediaFlowForward>,
) -> bool {
    let path = format!("/drive/v1/files/{file_id}");
    api_get(http, tokens, &path, &[], forward)
        .await
        .map(|data| data.get("error").is_none() && data["id"].as_str() == Some(file_id))
        .unwrap_or(false)
}

fn magnet_or_resource_url(value: &Value) -> Option<&str> {
    value
        .get("params")
        .and_then(|p| p.get("url"))
        .and_then(|u| {
            u.as_str()
                .or_else(|| u.get("url").and_then(|inner| inner.as_str()))
        })
        .or_else(|| value.get("original_url").and_then(|u| u.as_str()))
}

fn item_has_info_hash(item: &Value, info_hash: &str) -> bool {
    let hash = info_hash.to_lowercase();
    if item
        .get("hash")
        .and_then(|h| h.as_str())
        .is_some_and(|h| h.to_lowercase() == hash)
    {
        return true;
    }
    magnet_or_resource_url(item).is_some_and(|url| url.to_lowercase().contains(&hash))
}

fn task_has_info_hash(task: &Value, info_hash: &str) -> bool {
    if item_has_info_hash(task, info_hash) {
        return true;
    }
    task.get("reference_resource")
        .is_some_and(|resource| item_has_info_hash(resource, info_hash))
}

async fn cleanup_stale_error_tasks(
    http: &Client,
    tokens: &mut Tokens,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
) {
    let tasks = offline_tasks_for_hash(http, tokens, info_hash, forward)
        .await
        .unwrap_or_default();
    for task in tasks {
        if task_phase(&task) != "PHASE_TYPE_ERROR" {
            continue;
        }
        let msg = task["message"].as_str().unwrap_or("");
        if !is_recoverable_task_error(msg) {
            continue;
        }
        if let Some(task_id) = task["id"].as_str().filter(|s| !s.is_empty()) {
            tracing::debug!(
                task_id = %task_id,
                message = %msg,
                "PikPak stale offline task — deleting"
            );
            delete_offline_tasks(http, tokens, &[task_id], forward).await;
        }
    }
}

async fn get_file_detail(
    http: &Client,
    tokens: &mut Tokens,
    file_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let path = format!("/drive/v1/files/{file_id}");
    api_get(http, tokens, &path, &[], forward).await
}

async fn resolve_torrent_folder_id(
    http: &Client,
    tokens: &mut Tokens,
    my_pack_folder_id: &str,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<String>, ProviderError> {
    let tasks = offline_tasks_for_hash(http, tokens, info_hash, forward).await?;
    let mut sorted_tasks: Vec<_> = tasks.into_iter().collect();
    sorted_tasks.sort_by_key(|task| match task_phase(task) {
        "PHASE_TYPE_COMPLETE" => 0,
        "PHASE_TYPE_RUNNING" | "PHASE_TYPE_PENDING" => 1,
        _ => 2,
    });
    for task in &sorted_tasks {
        let Some(file_id) = task_file_id(task) else {
            continue;
        };
        if drive_file_exists(http, tokens, &file_id, forward).await {
            tracing::debug!(
                hash = %info_hash,
                file_id = %file_id,
                phase = task_phase(task),
                "PikPak torrent resolved via offline task file_id"
            );
            return Ok(Some(file_id));
        }
    }

    let files = {
        let complete =
            file_list_all(http, tokens, Some(my_pack_folder_id), "1000", None, forward).await?;
        if complete.iter().any(|f| item_has_info_hash(f, info_hash)) {
            complete
        } else {
            file_list_all_with_filters(
                http,
                tokens,
                Some(my_pack_folder_id),
                "1000",
                &trashed_only_file_filters(),
                forward,
            )
            .await?
        }
    };
    if let Some(item) = files.iter().find(|f| item_has_info_hash(f, info_hash)) {
        if let Some(id) = item["id"].as_str() {
            tracing::debug!(
                hash = %info_hash,
                file_id = %id,
                "PikPak torrent resolved via My Pack listing"
            );
            return Ok(Some(id.to_string()));
        }
    }

    // List responses often omit params.url/hash; the detail endpoint includes them.
    for item in &files {
        if item["kind"].as_str() != Some("drive#folder") {
            continue;
        }
        let Some(id) = item["id"].as_str() else {
            continue;
        };
        if item_has_info_hash(item, info_hash) {
            return Ok(Some(id.to_string()));
        }
        let detail = get_file_detail(http, tokens, id, forward).await?;
        if detail.get("error").is_none() && item_has_info_hash(&detail, info_hash) {
            tracing::debug!(
                hash = %info_hash,
                file_id = %id,
                "PikPak torrent resolved via My Pack folder detail"
            );
            return Ok(Some(id.to_string()));
        }
    }

    Ok(None)
}

fn task_phase(task: &Value) -> &str {
    task["phase"].as_str().unwrap_or("")
}

fn is_recoverable_task_error(msg: &str) -> bool {
    let lower = msg.to_lowercase();
    lower.contains("file deleted")
        || lower.contains("folder no longer exists")
        || lower.contains("folder does not exist")
        || lower.contains("parent folder not found")
        || msg == "Save failed, retry please"
}

async fn handle_torrent_error(
    http: &Client,
    tokens: &mut Tokens,
    torrent: &Value,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    let msg = torrent["message"].as_str().unwrap_or("");
    let task_id = torrent["id"].as_str().unwrap_or("");

    if is_recoverable_task_error(msg) {
        if !task_id.is_empty() {
            tracing::debug!(
                task_id = %task_id,
                message = %msg,
                "PikPak stale offline task — deleting so torrent can be re-added"
            );
            delete_offline_tasks(http, tokens, &[task_id], forward).await;
        }
        return Ok(());
    }

    match msg {
        "Storage space is not enough" | "Not enough storage space available" => {
            if !task_id.is_empty() {
                delete_offline_tasks(http, tokens, &[task_id], forward).await;
            }
            Err(ProviderError::api(
                "Not enough storage space in your PikPak account.",
                "not_enough_space.mp4",
            ))
        }
        "You have reached the limits of free usage today"
        | "The number of free transfers has been used up, continued use requires Premium"
        | "Insufficient cloud storage, continued use requires Premium" => {
            if !task_id.is_empty() {
                offline_task_retry(http, tokens, task_id, forward).await;
            }
            Err(ProviderError::api(
                "PikPak daily download limit reached.",
                "daily_download_limit.mp4",
            ))
        }
        other => Err(ProviderError::api(
            format!("Error downloading torrent: {other}"),
            "transfer_error.mp4",
        )),
    }
}

async fn offline_task_folder_exists(
    http: &Client,
    tokens: &mut Tokens,
    task: &Value,
    forward: Option<&MediaFlowForward>,
) -> bool {
    match task_file_id(task) {
        Some(file_id) if !file_id.is_empty() => {
            drive_file_exists(http, tokens, &file_id, forward).await
        }
        _ => false,
    }
}

async fn wait_for_torrent_to_complete(
    http: &Client,
    tokens: &mut Tokens,
    info_hash: &str,
    my_pack_folder_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    for _ in 0..MAX_RETRIES {
        if resolve_torrent_folder_id(http, tokens, my_pack_folder_id, info_hash, forward)
            .await?
            .is_some()
        {
            return Ok(());
        }

        let torrent = check_torrent_status(http, tokens, info_hash, forward).await?;
        match torrent {
            None => return Ok(()),
            Some(task) if offline_task_folder_exists(http, tokens, &task, forward).await => {
                return Ok(());
            }
            Some(task) if task["progress"].as_str() == Some("100") => {
                if offline_task_folder_exists(http, tokens, &task, forward).await
                    || resolve_torrent_folder_id(
                        http,
                        tokens,
                        my_pack_folder_id,
                        info_hash,
                        forward,
                    )
                    .await?
                    .is_some()
                {
                    return Ok(());
                }
                tokio::time::sleep(tokio::time::Duration::from_secs(RETRY_SECS)).await;
            }
            Some(task) if task_phase(&task) == "PHASE_TYPE_ERROR" => {
                handle_torrent_error(http, tokens, &task, forward).await?;
            }
            Some(task) if task_phase(&task) == "PHASE_TYPE_COMPLETE" => {
                if offline_task_folder_exists(http, tokens, &task, forward).await
                    || resolve_torrent_folder_id(
                        http,
                        tokens,
                        my_pack_folder_id,
                        info_hash,
                        forward,
                    )
                    .await?
                    .is_some()
                {
                    return Ok(());
                }
                tokio::time::sleep(tokio::time::Duration::from_secs(RETRY_SECS)).await;
            }
            Some(_) => {
                tokio::time::sleep(tokio::time::Duration::from_secs(RETRY_SECS)).await;
            }
        }
    }
    Err(ProviderError::api(
        "Torrent is still downloading in PikPak. Please try again later.",
        "torrent_not_downloaded.mp4",
    ))
}

async fn handle_torrent_status(
    http: &Client,
    tokens: &mut Tokens,
    info_hash: &str,
    my_pack_folder_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    cleanup_stale_error_tasks(http, tokens, info_hash, forward).await;

    if resolve_torrent_folder_id(http, tokens, my_pack_folder_id, info_hash, forward)
        .await?
        .is_some()
    {
        return Ok(());
    }

    let torrent = check_torrent_status(http, tokens, info_hash, forward).await?;
    let Some(task) = torrent else {
        return Ok(());
    };

    match task_phase(&task) {
        "PHASE_TYPE_ERROR" => handle_torrent_error(http, tokens, &task, forward).await?,
        "PHASE_TYPE_COMPLETE" if offline_task_folder_exists(http, tokens, &task, forward).await => {
            return Ok(());
        }
        "PHASE_TYPE_RUNNING" | "PHASE_TYPE_PENDING" => {
            if resolve_torrent_folder_id(http, tokens, my_pack_folder_id, info_hash, forward)
                .await?
                .is_some()
            {
                return Ok(());
            }
            wait_for_torrent_to_complete(http, tokens, info_hash, my_pack_folder_id, forward)
                .await?;
        }
        _ => {}
    }
    Ok(())
}

async fn offline_task_retry(
    http: &Client,
    tokens: &mut Tokens,
    task_id: &str,
    forward: Option<&MediaFlowForward>,
) {
    let body = json!({
        "type": "offline",
        "create_type": "RETRY",
        "id": task_id,
    });
    let _ = api_post(http, tokens, "/drive/v1/task", &body, forward).await;
}

// ─── File helpers (Python parity) ─────────────────────────────────────────────

fn is_video(name: &str) -> bool {
    let lower = name.to_lowercase();
    VIDEO_EXTS.iter().any(|e| lower.ends_with(&format!(".{e}")))
}

async fn file_list_page(
    http: &Client,
    tokens: &mut Tokens,
    parent_id: Option<&str>,
    limit: &str,
    page_token: Option<&str>,
    filters: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let mut params = vec![
        ("thumbnail_size", "SIZE_MEDIUM"),
        ("limit", limit),
        ("with_audit", "true"),
        ("filters", filters),
    ];
    if let Some(pid) = parent_id {
        params.push(("parent_id", pid));
    }
    if let Some(token) = page_token.filter(|s| !s.is_empty()) {
        params.push(("page_token", token));
    }
    api_get(http, tokens, "/drive/v1/files", &params, forward).await
}

async fn file_list_all_with_filters(
    http: &Client,
    tokens: &mut Tokens,
    parent_id: Option<&str>,
    limit: &str,
    filters: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<Value>, ProviderError> {
    let mut all = Vec::new();
    let mut page_token: Option<String> = None;
    loop {
        let data = file_list_page(
            http,
            tokens,
            parent_id,
            limit,
            page_token.as_deref(),
            filters,
            forward,
        )
        .await?;
        all.extend(data["files"].as_array().cloned().unwrap_or_default());
        page_token = data["next_page_token"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        if page_token.is_none() {
            break;
        }
    }
    Ok(all)
}

async fn file_list_page_legacy(
    http: &Client,
    tokens: &mut Tokens,
    parent_id: Option<&str>,
    limit: &str,
    page_token: Option<&str>,
    extra_filters: Option<&Value>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    file_list_page(
        http,
        tokens,
        parent_id,
        limit,
        page_token,
        &default_file_filters(extra_filters),
        forward,
    )
    .await
}

async fn file_list_all(
    http: &Client,
    tokens: &mut Tokens,
    parent_id: Option<&str>,
    limit: &str,
    extra_filters: Option<&Value>,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<Value>, ProviderError> {
    let mut all = Vec::new();
    let mut page_token: Option<String> = None;
    loop {
        let data = file_list_page_legacy(
            http,
            tokens,
            parent_id,
            limit,
            page_token.as_deref(),
            extra_filters,
            forward,
        )
        .await?;
        all.extend(data["files"].as_array().cloned().unwrap_or_default());
        page_token = data["next_page_token"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        if page_token.is_none() {
            break;
        }
    }
    Ok(all)
}

async fn file_list(
    http: &Client,
    tokens: &mut Tokens,
    parent_id: Option<&str>,
    limit: &str,
    extra_filters: Option<&Value>,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<Value>, ProviderError> {
    file_list_all(http, tokens, parent_id, limit, extra_filters, forward).await
}

async fn get_my_pack_folder_id(
    http: &Client,
    tokens: &mut Tokens,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let files = file_list(http, tokens, None, "100", None, forward).await?;
    files
        .iter()
        .find(|f| f["name"].as_str() == Some("My Pack"))
        .and_then(|f| f["id"].as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| ProviderError::api("PikPak 'My Pack' folder not found.", "api_error.mp4"))
}

async fn get_torrent_file_by_info_hash(
    http: &Client,
    tokens: &mut Tokens,
    my_pack_folder_id: &str,
    info_hash: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<Value>, ProviderError> {
    let Some(folder_id) =
        resolve_torrent_folder_id(http, tokens, my_pack_folder_id, info_hash, forward).await?
    else {
        return Ok(None);
    };
    let path = format!("/drive/v1/files/{folder_id}");
    let data = api_get(http, tokens, &path, &[], forward).await?;
    if data.get("error").is_some() {
        return Ok(None);
    }
    Ok(Some(data))
}

async fn get_files_from_folder(
    http: &Client,
    tokens: &mut Tokens,
    folder_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<Value>, ProviderError> {
    let contents = file_list(http, tokens, Some(folder_id), "100", None, forward).await?;
    let mut files = Vec::new();
    for item in contents {
        match item["kind"].as_str() {
            Some("drive#file") => files.push(item),
            Some("drive#folder") => {
                if let Some(id) = item["id"].as_str() {
                    let sub = Box::pin(get_files_from_folder(http, tokens, id, forward)).await?;
                    files.extend(sub);
                }
            }
            _ => {}
        }
    }
    Ok(files)
}

fn file_basename(name: &str) -> &str {
    std::path::Path::new(name)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or(name)
}

/// Pick the best file from a torrent folder (Python `select_file_index_from_torrent` parity).
fn select_video_file(
    files: &[(String, i64, String)],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<usize, ProviderError> {
    if files.is_empty() {
        return Err(ProviderError::api(
            "No valid video files found in torrent.",
            "no_matching_file.mp4",
        ));
    }

    if let Some(name) = filename {
        for (idx, (fname, _, _)) in files.iter().enumerate() {
            if file_basename(fname) == name {
                return Ok(idx);
            }
        }
    }

    if let Some(fi) = file_index {
        if fi >= 0 && (fi as usize) < files.len() {
            return Ok(fi as usize);
        }
    }

    let video_indices: Vec<usize> = files
        .iter()
        .enumerate()
        .filter(|(_, (name, _, _))| is_video(name))
        .map(|(i, _)| i)
        .collect();
    if video_indices.is_empty() {
        return Err(ProviderError::api(
            "No valid video files found in torrent.",
            "no_matching_file.mp4",
        ));
    }

    if let Some(name) = filename {
        let name_lower = name.to_lowercase();
        if let Some(idx) = files
            .iter()
            .position(|(n, _, _)| n.to_lowercase().contains(&name_lower))
        {
            return Ok(idx);
        }
    }

    if let (Some(s), Some(e)) = (season, episode) {
        let patterns = [
            format!("s{s:02}e{e:02}"),
            format!("{s}x{e:02}"),
            format!("{s:02}x{e:02}"),
        ];
        if let Some(&idx) = video_indices.iter().find(|&&i| {
            let lower = files[i].0.to_lowercase();
            patterns.iter().any(|p| lower.contains(p))
        }) {
            return Ok(idx);
        }
        return Err(ProviderError::api(
            "Found video files but couldn't match season/episode.",
            "episode_not_found.mp4",
        ));
    }

    video_indices
        .into_iter()
        .max_by_key(|&i| files[i].1)
        .ok_or_else(|| {
            ProviderError::api(
                "No valid video file found in torrent.",
                "no_matching_file.mp4",
            )
        })
}

async fn find_file_in_folder_tree(
    http: &Client,
    tokens: &mut Tokens,
    my_pack_folder_id: &str,
    info_hash: &str,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    forward: Option<&MediaFlowForward>,
) -> Result<Option<String>, ProviderError> {
    let torrent_file =
        get_torrent_file_by_info_hash(http, tokens, my_pack_folder_id, info_hash, forward).await?;
    let Some(torrent_file) = torrent_file else {
        return Ok(None);
    };

    let raw_files = if torrent_file["kind"].as_str() == Some("drive#file") {
        vec![torrent_file]
    } else if let Some(folder_id) = torrent_file["id"].as_str() {
        get_files_from_folder(http, tokens, folder_id, forward).await?
    } else {
        return Ok(None);
    };

    let files: Vec<(String, i64, String)> = raw_files
        .iter()
        .filter_map(|f| {
            let name = f["name"].as_str()?.to_string();
            let id = f["id"].as_str()?.to_string();
            let size = parse_pikpak_size(&f["size"]);
            Some((name, size, id))
        })
        .collect();

    let idx = select_video_file(&files, filename, file_index, season, episode)?;
    Ok(Some(files[idx].2.clone()))
}

// ─── Download URL (pikpakapi / debrify / alist parity) ───────────────────────

fn link_throughput_bytes(url: &str) -> i64 {
    for key in ["ms=", "th="] {
        if let Some(value) = url.split('&').find_map(|part| {
            part.strip_prefix(key)
                .and_then(|raw| raw.parse::<i64>().ok())
        }) {
            return value;
        }
    }
    0
}

fn media_link_score(media: &Value, url: &str) -> (i64, u8) {
    let mut preference = 0u8;
    if media["is_default"].as_bool() == Some(true) {
        preference += 2;
    }
    if media["is_origin"].as_bool() == Some(true) {
        preference += 2;
    }
    if media["is_visible"].as_bool() != Some(false) {
        preference += 1;
    }
    (link_throughput_bytes(url), preference)
}

fn pick_best_media_url(medias: &[Value]) -> Option<String> {
    medias
        .iter()
        .filter_map(|media| {
            let url = media["link"]["url"].as_str().filter(|s| !s.is_empty())?;
            Some((media_link_score(media, url), url.to_string()))
        })
        .max_by(|(a, _), (b, _)| a.cmp(b))
        .map(|(_, url)| url)
}

async fn get_about(
    http: &Client,
    tokens: &mut Tokens,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    api_get(http, tokens, "/drive/v1/about", &[], forward).await
}

const PLAYBACK_QUERY_FETCH: &[(&str, &str)] = &[
    ("_magic", "2021"),
    ("usage", "FETCH"),
    ("thumbnail_size", "SIZE_LARGE"),
    ("with_audit", "true"),
];
const PLAYBACK_QUERY_CACHE: &[(&str, &str)] = &[
    ("_magic", "2021"),
    ("usage", "CACHE"),
    ("thumbnail_size", "SIZE_LARGE"),
    ("with_audit", "true"),
];
const PLAYBACK_QUERY_AUDIT: &[(&str, &str)] = &[("with_audit", "true")];
const PLAYBACK_QUERY_PLAIN: &[(&str, &str)] = &[];

/// Classify account tier from `/drive/v1/about`.
///
/// Observed values: `user_type` 1 = premium (10 TB quota), 3 = free (6 GB quota).
fn about_is_premium(data: &Value) -> bool {
    match data["user_type"].as_i64() {
        Some(1) => return true,
        Some(3) => return false,
        _ => {}
    }

    if data["quotas"]["cloud_download"]["is_unlimited"].as_bool() == Some(true) {
        return true;
    }

    const PREMIUM_STORAGE_BYTES: i64 = 100 * 1024 * 1024 * 1024;
    parse_pikpak_size(&data["quota"]["limit"]) >= PREMIUM_STORAGE_BYTES
}

async fn account_is_premium(
    http: &Client,
    tokens: &mut Tokens,
    forward: Option<&MediaFlowForward>,
) -> Result<bool, ProviderError> {
    if let Some(is_premium) = tokens.is_premium {
        return Ok(is_premium);
    }
    let about = get_about(http, tokens, forward).await?;
    let is_premium = about_is_premium(&about);
    tokens.is_premium = Some(is_premium);
    tracing::debug!(
        premium = is_premium,
        user_type = about["user_type"].as_i64().unwrap_or(-1),
        storage_limit = parse_pikpak_size(&about["quota"]["limit"]),
        cloud_download_unlimited = about["quotas"]["cloud_download"]["is_unlimited"]
            .as_bool()
            .unwrap_or(false),
        "PikPak account tier resolved from /drive/v1/about"
    );
    Ok(is_premium)
}

fn playback_query_sets(premium: bool) -> [&'static [(&'static str, &'static str)]; 4] {
    if premium {
        [
            PLAYBACK_QUERY_CACHE,
            PLAYBACK_QUERY_FETCH,
            PLAYBACK_QUERY_AUDIT,
            PLAYBACK_QUERY_PLAIN,
        ]
    } else {
        [
            PLAYBACK_QUERY_FETCH,
            PLAYBACK_QUERY_CACHE,
            PLAYBACK_QUERY_AUDIT,
            PLAYBACK_QUERY_PLAIN,
        ]
    }
}

async fn fetch_file_playback_data(
    http: &Client,
    tokens: &mut Tokens,
    file_id: &str,
    premium: bool,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let path = format!("/drive/v1/files/{file_id}");
    let mut last = json!({});

    for params in playback_query_sets(premium) {
        let data = api_get(http, tokens, &path, params, forward).await?;
        if data["medias"]
            .as_array()
            .is_some_and(|medias| !medias.is_empty())
        {
            let usage = params
                .iter()
                .find(|(key, _)| *key == "usage")
                .map(|(_, value)| *value)
                .unwrap_or("default");
            tracing::debug!(
                file_id = %file_id,
                premium,
                usage,
                medias = data["medias"].as_array().map(|m| m.len()).unwrap_or(0),
                "PikPak file detail returned media links"
            );
            return Ok(data);
        }
        last = data;
    }

    Ok(last)
}

/// Resolve a playback URL for a concrete file ID.
///
/// Uses `/drive/v1/about` to pick the optimal file-detail mode:
/// premium → `usage=CACHE`, free → `usage=FETCH` (with fallbacks).
async fn get_download_url(
    http: &Client,
    tokens: &mut Tokens,
    file_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let action = format!("GET:/drive/v1/files/{file_id}");

    invalidate_captcha(tokens);
    ensure_captcha(http, tokens, &action).await?;

    let premium = account_is_premium(http, tokens, forward)
        .await
        .unwrap_or(false);
    let data = fetch_file_playback_data(http, tokens, file_id, premium, forward).await?;

    if let Some(medias) = data["medias"].as_array().filter(|m| !m.is_empty()) {
        if let Some(url) = pick_best_media_url(medias) {
            tracing::debug!(
                file_id = %file_id,
                premium,
                throughput_bytes = link_throughput_bytes(&url),
                "PikPak playback using media link"
            );
            return Ok(url);
        }
    }

    data["web_content_link"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(|s| {
            tracing::debug!(
                file_id = %file_id,
                throughput_bytes = link_throughput_bytes(s),
                "PikPak playback falling back to web_content_link"
            );
            s.to_string()
        })
        .ok_or_else(|| {
            ProviderError::api(
                "PikPak returned no download URL for this file.",
                "api_error.mp4",
            )
        })
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
    if lower.contains("daily")
        || lower.contains("free usage")
        || lower.contains("free transfers")
        || lower.contains("continued use requires")
    {
        return ProviderError::api(
            "PikPak daily download limit reached.",
            "daily_download_limit.mp4",
        );
    }
    if lower.contains("requires premium") {
        return ProviderError::api(
            "PikPak premium required for this operation.",
            "need_premium.mp4",
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

async fn add_magnet(
    http: &Client,
    tokens: &mut Tokens,
    magnet_link: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    let body = json!({
        "kind": "drive#file",
        "upload_type": "UPLOAD_TYPE_URL",
        "url": {"url": magnet_link},
        "folder_type": "DOWNLOAD",
    });
    let data = api_post(http, tokens, "/drive/v1/files", &body, forward).await?;
    if let Some(err) = data.get("error") {
        let msg = data["error_description"]
            .as_str()
            .unwrap_or_else(|| err.as_str().unwrap_or("Failed to add magnet"));
        return Err(map_pikpak_error(msg));
    }
    Ok(())
}

async fn retrieve_or_download_file(
    http: &Client,
    tokens: &mut Tokens,
    my_pack_folder_id: &str,
    info_hash: &str,
    magnet_link: &str,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    required_size: i64,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    if let Some(file_id) = find_file_in_folder_tree(
        http,
        tokens,
        my_pack_folder_id,
        info_hash,
        filename,
        file_index,
        season,
        episode,
        forward,
    )
    .await?
    {
        return Ok(file_id);
    }

    let active = check_torrent_status(http, tokens, info_hash, forward).await?;
    if let Some(task) = active {
        let phase = task_phase(&task);
        if phase == "PHASE_TYPE_RUNNING" || phase == "PHASE_TYPE_PENDING" {
            wait_for_torrent_to_complete(http, tokens, info_hash, my_pack_folder_id, forward)
                .await?;
            if let Some(file_id) = find_file_in_folder_tree(
                http,
                tokens,
                my_pack_folder_id,
                info_hash,
                filename,
                file_index,
                season,
                episode,
                forward,
            )
            .await?
            {
                return Ok(file_id);
            }
        } else if phase == "PHASE_TYPE_COMPLETE" {
            if let Some(file_id) = task_file_id(&task) {
                if drive_file_exists(http, tokens, &file_id, forward).await {
                    if let Some(selected) = find_file_in_folder_tree(
                        http,
                        tokens,
                        my_pack_folder_id,
                        info_hash,
                        filename,
                        file_index,
                        season,
                        episode,
                        forward,
                    )
                    .await?
                    {
                        return Ok(selected);
                    }
                }
            }
        }
    }

    if let Some(_folder_id) =
        resolve_torrent_folder_id(http, tokens, my_pack_folder_id, info_hash, forward).await?
    {
        return find_file_in_folder_tree(
            http,
            tokens,
            my_pack_folder_id,
            info_hash,
            filename,
            file_index,
            season,
            episode,
            forward,
        )
        .await?
        .ok_or_else(|| {
            ProviderError::api(
                "No valid video files found in torrent.",
                "no_matching_file.mp4",
            )
        });
    }

    free_up_space(http, tokens, required_size, forward).await;
    add_magnet(http, tokens, magnet_link, forward).await?;
    wait_for_torrent_to_complete(http, tokens, info_hash, my_pack_folder_id, forward).await?;

    find_file_in_folder_tree(
        http,
        tokens,
        my_pack_folder_id,
        info_hash,
        filename,
        file_index,
        season,
        episode,
        forward,
    )
    .await?
    .ok_or_else(|| {
        ProviderError::api(
            "Torrent is still downloading in PikPak. Please try again later.",
            "torrent_not_downloaded.mp4",
        )
    })
}

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
    required_size: Option<i64>,
    _user_ip: Option<&str>,
    forward: Option<&crate::providers::torrents::transport::MediaFlowForward>,
) -> Result<String, ProviderError> {
    let mut tokens = decode_token(token)?;
    let hash = info_hash.to_lowercase();
    let required_size = required_size.unwrap_or(0).max(0);

    let my_pack_id = get_my_pack_folder_id(http, &mut tokens, forward).await?;

    cleanup_stale_error_tasks(http, &mut tokens, &hash, forward).await;

    if let Some(file_id) = find_file_in_folder_tree(
        http,
        &mut tokens,
        &my_pack_id,
        &hash,
        filename,
        file_index,
        season,
        episode,
        forward,
    )
    .await?
    {
        return get_download_url(http, &mut tokens, &file_id, forward).await;
    }

    handle_torrent_status(http, &mut tokens, &hash, &my_pack_id, forward).await?;

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

    let selected_file_id = retrieve_or_download_file(
        http,
        &mut tokens,
        &my_pack_id,
        &hash,
        &magnet,
        filename,
        file_index,
        season,
        episode,
        required_size,
        forward,
    )
    .await?;

    get_download_url(http, &mut tokens, &selected_file_id, forward).await
}

async fn get_quota(
    http: &Client,
    tokens: &mut Tokens,
    forward: Option<&MediaFlowForward>,
) -> Result<(i64, i64), ProviderError> {
    let data = get_about(http, tokens, forward).await?;
    let limit = parse_pikpak_size(&data["quota"]["limit"]);
    let usage = parse_pikpak_size(&data["quota"]["usage"]);
    Ok((limit, usage))
}

async fn free_up_space(
    http: &Client,
    tokens: &mut Tokens,
    required_space: i64,
    forward: Option<&MediaFlowForward>,
) {
    let Ok((limit, usage)) = get_quota(http, tokens, forward).await else {
        return;
    };
    let mut available_space = (limit - usage).max(0);
    if available_space >= required_space {
        return;
    }

    let mut contents = file_list(http, tokens, Some("*"), "1000", None, forward)
        .await
        .unwrap_or_default();
    let trashed = file_list(
        http,
        tokens,
        Some("*"),
        "1000",
        Some(&json!({"trashed": {"eq": true}})),
        forward,
    )
    .await
    .unwrap_or_default();
    contents.extend(trashed);

    contents.sort_by(|a, b| {
        let a_trashed = a["trashed"].as_bool().unwrap_or(false);
        let b_trashed = b["trashed"].as_bool().unwrap_or(false);
        let a_size = parse_pikpak_size(&a["size"]);
        let b_size = parse_pikpak_size(&b["size"]);
        b_trashed.cmp(&a_trashed).then(b_size.cmp(&a_size))
    });

    for file in contents {
        if available_space >= required_space {
            break;
        }
        let Some(file_id) = file["id"].as_str() else {
            continue;
        };
        let parent_id = file["parent_id"].as_str().unwrap_or("");
        let mut ids = vec![file_id.to_string()];
        if !parent_id.is_empty() {
            ids.insert(0, parent_id.to_string());
        }
        let body = json!({ "ids": ids });
        if api_post(http, tokens, "/drive/v1/files:batchDelete", &body, forward)
            .await
            .is_ok()
        {
            available_space += parse_pikpak_size(&file["size"]);
        }
    }
}

async fn delete_offline_tasks(
    http: &Client,
    tokens: &mut Tokens,
    task_ids: &[&str],
    forward: Option<&MediaFlowForward>,
) {
    if task_ids.is_empty() {
        return;
    }
    let url = drive_url("/drive/v1/tasks");
    let mut query: Vec<(&str, String)> = task_ids
        .iter()
        .map(|id| ("task_ids", id.to_string()))
        .collect();
    query.push(("delete_files", "false".to_string()));
    let _ = http
        .delete(&url)
        .headers(build_api_headers(tokens))
        .query(&query)
        .send()
        .await;
    let _ = forward;
}

async fn trash_my_pack_files(
    http: &Client,
    tokens: &mut Tokens,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    let my_pack_id = get_my_pack_folder_id(http, tokens, forward).await?;
    let files = file_list(http, tokens, Some(&my_pack_id), "1000", None, forward).await?;
    let ids: Vec<String> = files
        .iter()
        .filter_map(|f| f["id"].as_str().map(str::to_string))
        .collect();
    if !ids.is_empty() {
        let body = json!({ "ids": ids });
        api_post(http, tokens, "/drive/v1/files:batchDelete", &body, forward)
            .await
            .ok();
    }
    Ok(())
}

pub async fn delete_all_torrents(http: &Client, token: &str) -> Result<(), ProviderError> {
    let mut tokens = decode_token(token)?;
    trash_my_pack_files(http, &mut tokens, None).await
}

pub async fn delete_torrent_by_hash(
    http: &Client,
    token: &str,
    info_hash: &str,
) -> Result<bool, ProviderError> {
    let mut tokens = decode_token(token)?;
    let hash = info_hash.to_lowercase();
    let my_pack_id = get_my_pack_folder_id(http, &mut tokens, None).await?;
    let item = get_torrent_file_by_info_hash(http, &mut tokens, &my_pack_id, &hash, None).await?;

    match item {
        None => Ok(false),
        Some(item) => {
            let file_id = item["id"].as_str().unwrap_or("").to_string();
            if !file_id.is_empty() {
                let body = serde_json::json!({ "ids": [file_id] });
                api_post(
                    http,
                    &mut tokens,
                    "/drive/v1/files:batchDelete",
                    &body,
                    None,
                )
                .await
                .ok();
            }
            Ok(true)
        }
    }
}

// ─── Torrent list ────────────────────────────────────────────────────────────

fn extract_btih(s: &str) -> Option<String> {
    let lower = s.to_lowercase();
    let prefix = "urn:btih:";
    let pos = lower.find(prefix)?;
    let rest = &s[pos + prefix.len()..];
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

fn extract_task_hash(task: &Value) -> Option<String> {
    if let Some(h) = task["hash"].as_str().filter(|s| s.len() >= 32) {
        return Some(h.to_lowercase());
    }
    if let Some(url) = magnet_or_resource_url(task) {
        if let Some(h) = extract_btih(url) {
            return Some(h);
        }
    }
    if let Some(h) = task["reference_resource"]["hash"]
        .as_str()
        .filter(|s| s.len() >= 32)
    {
        return Some(h.to_lowercase());
    }
    if let Some(url) = magnet_or_resource_url(&task["reference_resource"]) {
        if let Some(h) = extract_btih(url) {
            return Some(h);
        }
    }
    None
}

/// Return all completed offline tasks with their files, ready for the missing-import flow.
pub async fn list_downloaded_torrents(
    http: &Client,
    token: &str,
) -> Result<Vec<crate::providers::torrents::realdebrid::DownloadedTorrent>, ProviderError> {
    let mut tokens = decode_token(token)?;
    let tasks = offline_list(http, &mut tokens, &["PHASE_TYPE_COMPLETE"], None).await?;

    let mut results = Vec::new();
    for task in &tasks {
        let info_hash = match extract_task_hash(task) {
            Some(h) => h,
            None => continue,
        };
        let id = task["id"].as_str().unwrap_or("").to_string();
        let name = task["name"].as_str().unwrap_or(&info_hash).to_string();
        let size = parse_pikpak_size(&task["file_size"]);

        let folder_id = task_file_id(task);
        let raw_files: Vec<Value> =
            if let Some(fid) = folder_id.as_deref().filter(|s| !s.is_empty()) {
                if task["reference_resource"]["kind"].as_str() == Some("drive#file") {
                    vec![task["reference_resource"].clone()]
                } else {
                    get_files_from_folder(http, &mut tokens, fid, None)
                        .await
                        .unwrap_or_default()
                }
            } else {
                vec![]
            };

        let files: Vec<Value> = raw_files
            .iter()
            .filter_map(|f| {
                let n = f["name"].as_str()?;
                let s = parse_pikpak_size(&f["size"]);
                Some(serde_json::json!({"name": n, "size": s}))
            })
            .collect();

        results.push(crate::providers::torrents::realdebrid::DownloadedTorrent {
            id,
            info_hash,
            name,
            size,
            raw: serde_json::json!({ "files": files }),
        });
    }
    Ok(results)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn captcha_sign_matches_web_platform() {
        let sign = compute_captcha_sign("abc123deviceid000000000000000000", "1700000000000");
        assert!(sign.starts_with("1."));
        assert_eq!(sign.len(), 34); // "1." + 32 hex chars
    }

    #[test]
    fn token_roundtrip_includes_session_fields() {
        let tokens = Tokens {
            access_token: "access".into(),
            refresh_token: "refresh".into(),
            device_id: "device".into(),
            user_id: "user".into(),
            captcha_token: Some("captcha".into()),
            is_premium: None,
        };
        let encoded = encode_token(&tokens);
        let decoded = decode_token(&encoded).unwrap();
        assert_eq!(decoded.access_token, "access");
        assert_eq!(decoded.device_id, "device");
        assert_eq!(decoded.user_id, "user");
        assert_eq!(decoded.captcha_token.as_deref(), Some("captcha"));
    }

    #[test]
    fn legacy_token_is_detected_and_rejected() {
        let legacy = STANDARD.encode(r#"{"access_token":"a","refresh_token":"r"}"#.as_bytes());
        assert!(is_legacy_token(&legacy));
        assert!(decode_token(&legacy).is_err());
    }

    #[test]
    fn recoverable_task_errors_include_file_deleted() {
        assert!(is_recoverable_task_error("File deleted"));
        assert!(is_recoverable_task_error("Save failed, retry please"));
        assert!(!is_recoverable_task_error("Storage space is not enough"));
    }

    #[test]
    fn item_has_info_hash_matches_multiple_fields() {
        let hash = "f14edc61dedf9c20cc9c517cda810d86c668443d";
        assert!(item_has_info_hash(
            &json!({"hash": "F14EDC61DEDF9C20CC9C517CDA810D86C668443D"}),
            hash
        ));
        assert!(item_has_info_hash(
            &json!({"params": {"url": format!("magnet:?xt=urn:btih:{hash}")}}),
            hash
        ));
        assert!(item_has_info_hash(
            &json!({"params": {"url": {"url": format!("magnet:?xt=urn:btih:{hash}")}}}),
            hash
        ));
        assert!(task_has_info_hash(
            &json!({"reference_resource": {"hash": hash}}),
            hash
        ));
    }

    #[test]
    fn about_is_premium_matches_observed_account_profiles() {
        assert!(!about_is_premium(&json!({
            "user_type": 3,
            "quota": {"limit": "6442450944", "is_unlimited": false},
            "quotas": {"cloud_download": {"is_unlimited": false, "limit": "3"}}
        })));
        assert!(about_is_premium(&json!({
            "user_type": 1,
            "quota": {"limit": "10995116277760", "is_unlimited": false},
            "quotas": {"cloud_download": {"is_unlimited": true, "limit": "-1"}}
        })));
    }

    #[test]
    fn playback_query_sets_order_by_account_type() {
        assert_eq!(playback_query_sets(true)[0], PLAYBACK_QUERY_CACHE);
        assert_eq!(playback_query_sets(false)[0], PLAYBACK_QUERY_FETCH);
    }

    #[test]
    fn pick_best_media_url_prefers_higher_throughput() {
        let medias = vec![
            json!({
                "link": {"url": "https://cdn.example/a?ms=6291456&th=6291456"},
                "is_default": false,
            }),
            json!({
                "link": {"url": "https://cdn.example/b?ms=37800000&th=37800000"},
                "is_default": true,
                "is_origin": true,
            }),
        ];
        assert_eq!(
            pick_best_media_url(&medias).as_deref(),
            Some("https://cdn.example/b?ms=37800000&th=37800000")
        );
    }
}
