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
    engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
    Engine,
};
use reqwest::Client;
use serde_json::{json, Value};

use crate::providers::{
    torrents::transport::{append_query, MediaFlowForward},
    ProviderError,
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
    !v.get("device_id")
        .and_then(|d| d.as_str())
        .is_some_and(|s| !s.is_empty())
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

    let url = format!("{}/v1/shield/captcha/init?client_id={CLIENT_ID}", user_base());
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
        || data["error"].as_str().is_some_and(|e| e == "unauthenticated")
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
        return Err(ProviderError::api("PikPak internal API error.", "api_error.mp4"));
    };

    Ok(data)
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

// ─── Task helpers ─────────────────────────────────────────────────────────────

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

/// Fetch file details with `usage=FETCH` so medias/web_content_link are populated.
async fn get_download_url(
    http: &Client,
    tokens: &mut Tokens,
    file_id: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    let path = format!("/drive/v1/files/{file_id}");
    let params = [
        ("usage", "FETCH"),
        ("_magic", "2021"),
        ("thumbnail_size", "SIZE_LARGE"),
        ("with_audit", "true"),
    ];
    let data = api_get(http, tokens, &path, &params, forward).await?;

    if let Some(medias) = data["medias"].as_array() {
        for media in medias {
            if media["is_default"].as_bool() == Some(true) || media["is_origin"].as_bool() == Some(true) {
                if let Some(url) = media["link"]["url"].as_str().filter(|s| !s.is_empty()) {
                    return Ok(url.to_string());
                }
            }
        }
        for media in medias {
            if let Some(url) = media["link"]["url"].as_str().filter(|s| !s.is_empty()) {
                return Ok(url.to_string());
            }
        }
    }

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

    let tasks = offline_list(
        http,
        &mut tokens,
        &["PHASE_TYPE_RUNNING", "PHASE_TYPE_ERROR"],
        forward,
    )
    .await?;
    let mut failed_task_id: Option<String> = None;
    let mut should_clear_space = false;
    for task in &tasks {
        if !task_has_info_hash(task, &hash) {
            continue;
        }
        if task_is_error(task) {
            let msg = task["message"]
                .as_str()
                .unwrap_or("Error downloading torrent");
            let msg_lower = msg.to_lowercase();
            tracing::debug!(
                hash = %hash,
                task_id = task["id"].as_str().unwrap_or(""),
                phase = task["phase"].as_str().unwrap_or(""),
                message = %msg,
                "PikPak error task found"
            );
            if msg_lower.contains("storage") || msg_lower.contains("not enough space") {
                failed_task_id = task["id"].as_str().map(str::to_string);
                should_clear_space = true;
            } else if msg_lower.contains("too frequent") || msg_lower.contains("try again later") {
                tracing::debug!(hash = %hash, message = %msg, "PikPak rate-limited task; will delete and retry");
                failed_task_id = task["id"].as_str().map(str::to_string);
            } else {
                return Err(map_pikpak_error(msg));
            }
        }
        if task_is_downloading(task) {
            return Err(ProviderError::api(
                "Torrent is still downloading in PikPak. Please try again later.",
                "torrent_not_downloaded.mp4",
            ));
        }
    }

    let my_pack_id = get_my_pack_folder_id(http, &mut tokens, forward).await?;
    let torrent_item = find_torrent_item(http, &mut tokens, &my_pack_id, &hash, forward).await?;

    if let Some(item) = torrent_item {
        let file_id = item["id"].as_str().unwrap_or("").to_string();
        let kind = item["kind"].as_str().unwrap_or("");
        let file_name = item["name"].as_str().unwrap_or("").to_string();

        let selected_id = if kind == "drive#folder" {
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
            file_id
        } else {
            return Err(ProviderError::api(
                "No video file found in PikPak torrent.",
                "no_matching_file.mp4",
            ));
        };

        return get_download_url(http, &mut tokens, &selected_id, forward).await;
    }

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

    if let Some(ref task_id) = failed_task_id {
        delete_offline_tasks(http, &mut tokens, &[task_id.as_str()], forward).await;
    }

    if should_clear_space {
        ensure_enough_space(http, &mut tokens, 0, forward).await;
    }

    let magnet_body = json!({
        "kind": "drive#file",
        "name": "",
        "upload_type": "UPLOAD_TYPE_URL",
        "url": {"url": magnet},
        "folder_type": "",
    });

    let add_result = api_post(http, &mut tokens, "/drive/v1/files", &magnet_body, forward).await;
    let add_resp = match add_result {
        Ok(v) => v,
        Err(e) => {
            let msg = e.to_string().to_lowercase();
            if msg.contains("storage") || msg.contains("not enough space") {
                trash_my_pack_files(http, &mut tokens, forward).await.ok();
                api_post(http, &mut tokens, "/drive/v1/files", &magnet_body, forward)
                    .await
                    .map_err(|e2| {
                        let m = e2.to_string().to_lowercase();
                        if m.contains("storage") || m.contains("not enough space") {
                            ProviderError::api(
                                "Not enough storage space in your PikPak account even after cleanup.",
                                "not_enough_space.mp4",
                            )
                        } else {
                            ProviderError::api(
                                format!("Failed to add torrent to PikPak: {e2}"),
                                "transfer_error.mp4",
                            )
                        }
                    })?
            } else if msg.contains("daily") || msg.contains("free usage") {
                return Err(ProviderError::api(
                    "PikPak daily download limit reached.",
                    "daily_download_limit.mp4",
                ));
            } else {
                return Err(ProviderError::api(
                    format!("Failed to add torrent to PikPak: {e}"),
                    "transfer_error.mp4",
                ));
            }
        }
    };

    if let Some(err) = add_resp["error"].as_str() {
        tracing::debug!(hash = %hash, error = %err, "PikPak add-magnet inline error");
        return Err(map_pikpak_error(err));
    }

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
                tracing::debug!(
                    hash = %hash,
                    task_id = task["id"].as_str().unwrap_or(""),
                    message = %msg,
                    "PikPak polling loop: error task"
                );
                return Err(map_pikpak_error(msg));
            }
            if task_is_complete(task) {
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

async fn get_quota(
    http: &Client,
    tokens: &mut Tokens,
    forward: Option<&MediaFlowForward>,
) -> Result<(i64, i64), ProviderError> {
    let data = api_get(http, tokens, "/drive/v1/about", &[], forward).await?;
    let limit = data["quota"]["limit"]
        .as_str()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0i64);
    let usage = data["quota"]["usage"]
        .as_str()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0i64);
    let usage_in_trash = data["quota"]["usage_in_trash"]
        .as_str()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0i64);
    Ok((limit, usage + usage_in_trash))
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

async fn ensure_enough_space(
    http: &Client,
    tokens: &mut Tokens,
    minimum: i64,
    forward: Option<&MediaFlowForward>,
) {
    let minimum = if minimum > 0 { minimum } else { 1_073_741_824 };

    let (limit, usage) = match get_quota(http, tokens, forward).await {
        Ok(q) => q,
        Err(e) => {
            tracing::warn!("PikPak: could not fetch quota for space check: {e}");
            return;
        }
    };

    if limit == 0 {
        tracing::warn!("PikPak: storage quota unavailable; skipping space check");
        return;
    }

    let free = (limit - usage).max(0);
    if free >= minimum {
        return;
    }

    tracing::info!(
        "PikPak: only {free} bytes free (need {minimum}) — clearing My Pack to free space"
    );
    trash_my_pack_files(http, tokens, forward).await.ok();
}

async fn trash_my_pack_files(
    http: &Client,
    tokens: &mut Tokens,
    forward: Option<&MediaFlowForward>,
) -> Result<(), ProviderError> {
    let my_pack_id = get_my_pack_folder_id(http, tokens, forward).await?;

    let filters = serde_json::to_string(&json!({"trashed": {"eq": false}})).unwrap_or_default();
    let data = api_get(
        http,
        tokens,
        "/drive/v1/files",
        &[
            ("parent_id", my_pack_id.as_str()),
            ("limit", "1000"),
            ("filters", &filters),
        ],
        forward,
    )
    .await?;

    let ids: Vec<String> = data["files"]
        .as_array()
        .unwrap_or(&vec![])
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
    let item = find_torrent_item(http, &mut tokens, &my_pack_id, &hash, None).await?;

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
}
