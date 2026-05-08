/// Integrations endpoints: Trakt/SIMKL OAuth + Telegram channel management.
///
/// Routes (prefix /api/v1/integrations):
///   GET    /                              → list_integrations
///   GET    /{platform}/status             → get_sync_status
///   GET    /oauth/{platform}/url          → get_oauth_url
///   GET    /simkl/callback                → simkl_oauth_callback
///   POST   /trakt/connect                 → connect_trakt
///   POST   /simkl/connect                 → connect_simkl
///   DELETE /{platform}/disconnect         → disconnect_integration
///   PATCH  /{platform}/settings           → update_integration_settings
///   POST   /{platform}/sync               → trigger_sync
///   POST   /sync-all                      → trigger_sync_all
///
/// Routes (prefix /api/v1/telegram):
///   GET    /status                        → get_telegram_status
///   GET    /config                        → get_telegram_config
///   PATCH  /config                        → update_telegram_config
///   POST   /channels                      → add_telegram_channel
///   DELETE /channels/{channel_id}         → remove_telegram_channel
///   PATCH  /channels/{channel_id}         → update_telegram_channel
///   POST   /validate                      → validate_telegram_channel
///   GET    /login                         → telegram_login
///   DELETE /unlink                        → telegram_unlink
///
/// Complex OAuth exchange and sync operations proxy to Python when
/// `python_proxy_url` is configured. Simple DB CRUD runs natively.

use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Redirect, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helpers ─────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
    let token = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(str::to_string)?;
    let dot = token.rfind('.')?;
    let (payload_str, sig) = token.split_at(dot);
    let sig = &sig[1..];
    let mut mac = Hmac::<Sha256>::new_from_slice(secret_key.as_bytes()).ok()?;
    mac.update(payload_str.as_bytes());
    let expected: String = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if expected != sig {
        return None;
    }
    let decoded = URL_SAFE_NO_PAD.decode(payload_str).ok()?;
    let data: serde_json::Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    data["sub"].as_str()?.parse::<i32>().ok()
}

fn unauthorized() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({"error": "Unauthorized"})),
    )
        .into_response()
}

fn not_found(msg: &str) -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({"error": msg})),
    )
        .into_response()
}

fn bad_request(msg: &str) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(serde_json::json!({"error": msg})),
    )
        .into_response()
}

fn db_error(context: &str, e: &sqlx::Error) -> Response {
    tracing::error!("{context}: {e}");
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"error": "Database error"})),
    )
        .into_response()
}

// ─── Proxy helper ─────────────────────────────────────────────────────────────

/// Forward a request verbatim to the Python service and stream the response back.
/// Used for operations that need Python-only integrations (OAuth token exchange, sync).
async fn proxy_to_python(
    state: &AppState,
    method: reqwest::Method,
    path: &str,
    headers: &HeaderMap,
    body: Option<serde_json::Value>,
    query: Option<&str>,
) -> Response {
    let Some(ref base) = state.config.python_proxy_url else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"error": "Feature requires Python service (set PYTHON_PROXY_URL)"})),
        )
            .into_response();
    };

    let url = if let Some(q) = query {
        format!("{}{path}?{q}", base.trim_end_matches('/'))
    } else {
        format!("{}{path}", base.trim_end_matches('/'))
    };

    let mut req = state.http.request(method, &url);

    // Forward Authorization header
    if let Some(auth) = headers.get("authorization") {
        req = req.header("Authorization", auth);
    }

    if let Some(b) = body {
        req = req.json(&b);
    }

    match req.send().await {
        Ok(resp) => {
            let status = StatusCode::from_u16(resp.status().as_u16())
                .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
            let body = resp.bytes().await.unwrap_or_default();
            (status, body).into_response()
        }
        Err(e) => {
            tracing::error!("Python proxy error: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({"error": "Upstream service unavailable"})),
            )
                .into_response()
        }
    }
}

// ─── DB row types ─────────────────────────────────────────────────────────────

/// (id, profile_id, platform, is_enabled, sync_direction, scrobble_enabled,
///  last_sync_at, last_sync_status, last_sync_error, last_sync_stats)
type IntegrationRow = (
    i32,
    i32,
    String,
    bool,
    String,
    bool,
    Option<DateTime<Utc>>,
    Option<String>,
    Option<String>,
    Option<serde_json::Value>,
);

#[derive(Serialize)]
struct IntegrationStatus {
    platform: String,
    connected: bool,
    is_enabled: bool,
    sync_direction: String,
    scrobble_enabled: bool,
    last_sync_at: Option<DateTime<Utc>>,
    last_sync_status: Option<String>,
    last_sync_error: Option<String>,
    last_sync_stats: Option<serde_json::Value>,
}

// ─── Request types ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ProfileIdQuery {
    pub profile_id: Option<i32>,
}

#[derive(Deserialize)]
pub struct OAuthUrlQuery {
    pub client_id: Option<String>,
}

#[derive(Deserialize)]
pub struct SimklCallbackQuery {
    pub code: Option<String>,
    pub error: Option<String>,
    pub error_description: Option<String>,
    pub state: Option<String>,
}

#[derive(Deserialize)]
pub struct TraktConnectRequest {
    pub code: String,
    pub client_id: Option<String>,
    pub client_secret: Option<String>,
}

#[derive(Deserialize)]
pub struct SimklConnectRequest {
    pub code: String,
    pub client_id: Option<String>,
    pub client_secret: Option<String>,
}

#[derive(Deserialize)]
pub struct IntegrationSettingsUpdate {
    pub is_enabled: Option<bool>,
    pub sync_direction: Option<String>,
    pub scrobble_enabled: Option<bool>,
    pub settings: Option<serde_json::Value>,
}

#[derive(Deserialize)]
pub struct TriggerSyncQuery {
    pub profile_id: Option<i32>,
    pub direction: Option<String>,
    #[serde(default)]
    pub full_sync: bool,
}

// ─── Integration endpoints ─────────────────────────────────────────────────────

const KNOWN_PLATFORMS: &[&str] = &["trakt", "simkl"];

/// GET /api/v1/integrations?profile_id=N
pub async fn list_integrations(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<ProfileIdQuery>,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    // Resolve profile_id: use provided or fall back to default profile
    let resolved_profile_id: i32 = if let Some(pid) = params.profile_id {
        let owns: Option<(i32,)> =
            match sqlx::query_as("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
                .bind(pid)
                .bind(user_id)
                .fetch_optional(&state.pool_ro)
                .await
            {
                Ok(r) => r,
                Err(e) => return db_error("list_integrations profile check", &e),
            };
        if owns.is_none() {
            return not_found("Profile not found");
        }
        pid
    } else {
        // Use default profile
        let default: Option<(i32,)> =
            match sqlx::query_as("SELECT id FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1")
                .bind(user_id)
                .fetch_optional(&state.pool_ro)
                .await
            {
                Ok(r) => r,
                Err(_) => None,
            };
        match default {
            Some((id,)) => id,
            None => return not_found("No default profile found"),
        }
    };

    let rows: Vec<IntegrationRow> = match sqlx::query_as(
        r#"SELECT id, profile_id, platform, is_enabled, sync_direction, scrobble_enabled,
                  last_sync_at, last_sync_status, last_sync_error, last_sync_stats
           FROM profile_integration
           WHERE profile_id = $1"#,
    )
    .bind(resolved_profile_id)
    .fetch_all(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => return db_error("list_integrations fetch", &e),
    };

    // Build a map of platform → row
    let mut map: std::collections::HashMap<String, &IntegrationRow> = std::collections::HashMap::new();
    for row in &rows {
        map.insert(row.2.clone(), row);
    }

    let integrations: Vec<IntegrationStatus> = KNOWN_PLATFORMS
        .iter()
        .map(|&platform| {
            if let Some(row) = map.get(platform) {
                IntegrationStatus {
                    platform: platform.to_string(),
                    connected: true,
                    is_enabled: row.3,
                    sync_direction: row.4.clone(),
                    scrobble_enabled: row.5,
                    last_sync_at: row.6,
                    last_sync_status: row.7.clone(),
                    last_sync_error: row.8.clone(),
                    last_sync_stats: row.9.clone(),
                }
            } else {
                IntegrationStatus {
                    platform: platform.to_string(),
                    connected: false,
                    is_enabled: false,
                    sync_direction: "two_way".to_string(),
                    scrobble_enabled: true,
                    last_sync_at: None,
                    last_sync_status: None,
                    last_sync_error: None,
                    last_sync_stats: None,
                }
            }
        })
        .collect();

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "profile_id": resolved_profile_id,
            "integrations": integrations,
        })),
    )
        .into_response()
}

/// GET /api/v1/integrations/{platform}/status?profile_id=N
pub async fn get_sync_status(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(platform): Path<String>,
    Query(params): Query<ProfileIdQuery>,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let owns: Option<(i32,)> =
        match sqlx::query_as("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
            .bind(params.profile_id.unwrap_or(0))
            .bind(user_id)
            .fetch_optional(&state.pool_ro)
            .await
        {
            Ok(r) => r,
            Err(e) => return db_error("get_sync_status profile check", &e),
        };
    if owns.is_none() {
        return not_found("Profile not found");
    }

    type SyncRow = (
        Option<DateTime<Utc>>,
        Option<String>,
        Option<String>,
        Option<serde_json::Value>,
    );
    let row: Option<SyncRow> = match sqlx::query_as(
        r#"SELECT last_sync_at, last_sync_status, last_sync_error, last_sync_stats
           FROM profile_integration
           WHERE profile_id = $1 AND platform = $2"#,
    )
    .bind(params.profile_id.unwrap_or(0))
    .bind(&platform)
    .fetch_optional(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => return db_error("get_sync_status fetch", &e),
    };

    let (last_sync_at, last_sync_status, last_sync_error, last_sync_stats) =
        row.unwrap_or((None, None, None, None));

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "platform": platform,
            "last_sync_at": last_sync_at,
            "last_sync_status": last_sync_status,
            "last_sync_error": last_sync_error,
            "last_sync_stats": last_sync_stats,
        })),
    )
        .into_response()
}

/// GET /api/v1/integrations/oauth/{platform}/url
pub async fn get_oauth_url(
    State(state): State<Arc<AppState>>,
    Path(platform): Path<String>,
    Query(params): Query<OAuthUrlQuery>,
) -> Response {
    // Proxy to Python for OAuth URL generation (needs platform secrets from Python config)
    let q = params
        .client_id
        .as_deref()
        .map(|id| format!("client_id={}", urlencoding::encode(id)))
        .unwrap_or_default();
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        &format!("/api/v1/integrations/oauth/{platform}/url"),
        &HeaderMap::new(),
        None,
        if q.is_empty() { None } else { Some(&q) },
    )
    .await
}

/// GET /api/v1/integrations/simkl/callback
pub async fn simkl_oauth_callback(
    State(state): State<Arc<AppState>>,
    Query(params): Query<SimklCallbackQuery>,
) -> Response {
    let mut query_parts: Vec<String> = vec!["simkl_oauth=1".to_string()];

    if let Some(ref code) = params.code {
        query_parts.push(format!("simkl_code={}", urlencoding::encode(code)));
    }
    if let Some(ref error) = params.error {
        query_parts.push(format!("simkl_error={}", urlencoding::encode(error)));
    }
    if let Some(ref desc) = params.error_description {
        query_parts.push(format!(
            "simkl_error_description={}",
            urlencoding::encode(desc)
        ));
    }
    if let Some(ref s) = params.state {
        query_parts.push(format!("simkl_state={}", urlencoding::encode(s)));
    }

    if params.code.is_none() && params.error.is_none() {
        query_parts.push("simkl_error=missing_code".to_string());
        query_parts.push("simkl_error_description=Missing+authorization+code+in+callback.".to_string());
    }

    let host = state.config.host_url.trim_end_matches('/');
    let base = if host.ends_with("/app") {
        format!("{host}/dashboard/integrations")
    } else {
        format!("{host}/app/dashboard/integrations")
    };

    let redirect_url = format!("{}?{}", base, query_parts.join("&"));
    Redirect::temporary(&redirect_url).into_response()
}

/// POST /api/v1/integrations/trakt/connect?profile_id=N
pub async fn connect_trakt(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<ProfileIdQuery>,
    Json(body): Json<TraktConnectRequest>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    let q = format!("profile_id={}", params.profile_id.unwrap_or(0));
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/integrations/trakt/connect",
        &headers,
        Some(serde_json::json!({
            "code": body.code,
            "client_id": body.client_id,
            "client_secret": body.client_secret,
        })),
        Some(&q),
    )
    .await
}

/// POST /api/v1/integrations/simkl/connect?profile_id=N
pub async fn connect_simkl(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<ProfileIdQuery>,
    Json(body): Json<SimklConnectRequest>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    let q = format!("profile_id={}", params.profile_id.unwrap_or(0));
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/integrations/simkl/connect",
        &headers,
        Some(serde_json::json!({
            "code": body.code,
            "client_id": body.client_id,
            "client_secret": body.client_secret,
        })),
        Some(&q),
    )
    .await
}

/// DELETE /api/v1/integrations/{platform}/disconnect?profile_id=N
pub async fn disconnect_integration(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(platform): Path<String>,
    Query(params): Query<ProfileIdQuery>,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    // Verify profile ownership
    let owns: Option<(i32,)> =
        match sqlx::query_as("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
            .bind(params.profile_id.unwrap_or(0))
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => return db_error("disconnect_integration profile check", &e),
        };
    if owns.is_none() {
        return not_found("Profile not found");
    }

    let exists: Option<(i32,)> = match sqlx::query_as(
        "SELECT id FROM profile_integration WHERE profile_id = $1 AND platform = $2",
    )
    .bind(params.profile_id.unwrap_or(0))
    .bind(&platform)
    .fetch_optional(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => return db_error("disconnect_integration fetch", &e),
    };

    if exists.is_none() {
        return not_found("Integration not connected");
    }

    if let Err(e) =
        sqlx::query("DELETE FROM profile_integration WHERE profile_id = $1 AND platform = $2")
            .bind(params.profile_id.unwrap_or(0))
            .bind(&platform)
            .execute(&state.pool)
            .await
    {
        return db_error("disconnect_integration delete", &e);
    }

    (
        StatusCode::OK,
        Json(serde_json::json!({"message": format!("{platform} disconnected successfully")})),
    )
        .into_response()
}

/// PATCH /api/v1/integrations/{platform}/settings?profile_id=N
pub async fn update_integration_settings(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(platform): Path<String>,
    Query(params): Query<ProfileIdQuery>,
    Json(body): Json<IntegrationSettingsUpdate>,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let owns: Option<(i32,)> =
        match sqlx::query_as("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
            .bind(params.profile_id.unwrap_or(0))
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => return db_error("update_integration_settings profile check", &e),
        };
    if owns.is_none() {
        return not_found("Profile not found");
    }

    // Get current integration
    type IntegRow = (i32, serde_json::Value);
    let row: Option<IntegRow> = match sqlx::query_as(
        "SELECT id, settings FROM profile_integration WHERE profile_id = $1 AND platform = $2",
    )
    .bind(params.profile_id.unwrap_or(0))
    .bind(&platform)
    .fetch_optional(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => return db_error("update_integration_settings fetch", &e),
    };

    let Some((integ_id, existing_settings)) = row else {
        return not_found("Integration not connected");
    };

    // Build dynamic update
    let mut sets: Vec<String> = Vec::new();
    let mut idx: i32 = 1;

    if body.is_enabled.is_some() {
        sets.push(format!("is_enabled = ${idx}"));
        idx += 1;
    }
    if body.sync_direction.is_some() {
        sets.push(format!("sync_direction = ${idx}"));
        idx += 1;
    }
    if body.scrobble_enabled.is_some() {
        sets.push(format!("scrobble_enabled = ${idx}"));
        idx += 1;
    }
    if body.settings.is_some() {
        sets.push(format!("settings = settings || ${idx}::jsonb"));
        idx += 1;
    }

    if !sets.is_empty() {
        let sql = format!(
            "UPDATE profile_integration SET {} WHERE id = ${idx}",
            sets.join(", ")
        );
        let mut q = sqlx::query(&sql);
        if let Some(v) = body.is_enabled {
            q = q.bind(v);
        }
        if let Some(ref v) = body.sync_direction {
            q = q.bind(v);
        }
        if let Some(v) = body.scrobble_enabled {
            q = q.bind(v);
        }
        if let Some(ref v) = body.settings {
            q = q.bind(v);
        }
        q = q.bind(integ_id);

        if let Err(e) = q.execute(&state.pool).await {
            return db_error("update_integration_settings execute", &e);
        }
    }

    (
        StatusCode::OK,
        Json(serde_json::json!({"message": "Settings updated successfully"})),
    )
        .into_response()
}

/// POST /api/v1/integrations/{platform}/sync?profile_id=N
pub async fn trigger_sync(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(platform): Path<String>,
    Query(params): Query<TriggerSyncQuery>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    // Sync operations require Python service (token exchange, Trakt/Simkl APIs, etc.)
    let mut q = format!("profile_id={}", params.profile_id.unwrap_or(0));
    if let Some(ref dir) = params.direction {
        q.push_str(&format!("&direction={}", urlencoding::encode(dir)));
    }
    if params.full_sync {
        q.push_str("&full_sync=true");
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        &format!("/api/v1/integrations/{platform}/sync"),
        &headers,
        None,
        Some(&q),
    )
    .await
}

/// POST /api/v1/integrations/sync-all?profile_id=N
pub async fn trigger_sync_all(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<ProfileIdQuery>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    let q = format!("profile_id={}", params.profile_id.unwrap_or(0));
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/integrations/sync-all",
        &headers,
        None,
        Some(&q),
    )
    .await
}

// ─── Telegram channel endpoints ───────────────────────────────────────────────

/// GET /api/v1/telegram/status
pub async fn get_telegram_status(State(state): State<Arc<AppState>>) -> Response {
    let scraper_enabled = state.telegram.is_some();
    let bot_configured = state.config.telegram_bot_token.is_some();
    let api_configured = state.config.telegram_api_id.is_some();
    let global_channels_count = state.config.telegram_scraping_channels.len();

    let message = if !scraper_enabled {
        "Telegram scraping is disabled by administrator".to_string()
    } else if !api_configured {
        "Telegram API credentials are not configured".to_string()
    } else if global_channels_count == 0 {
        "No global channels configured. Users can add their own channels.".to_string()
    } else {
        format!(
            "Telegram scraping is enabled with {global_channels_count} global channel(s)"
        )
    };

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "scraper_enabled": scraper_enabled,
            "bot_configured": bot_configured,
            "api_credentials_configured": api_configured,
            "global_channels_count": global_channels_count,
            "message": message,
        })),
    )
        .into_response()
}

/// GET /api/v1/telegram/config
pub async fn get_telegram_config(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    // Telegram config is stored in encrypted UserData (Python-only); proxy
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/telegram/config",
        &headers,
        None,
        None,
    )
    .await
}

/// PATCH /api/v1/telegram/config
pub async fn update_telegram_config(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    proxy_to_python(
        &state,
        reqwest::Method::PATCH,
        "/api/v1/telegram/config",
        &headers,
        Some(body),
        None,
    )
    .await
}

/// POST /api/v1/telegram/channels
pub async fn add_telegram_channel(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/telegram/channels",
        &headers,
        Some(body),
        None,
    )
    .await
}

/// DELETE /api/v1/telegram/channels/{channel_id}
pub async fn remove_telegram_channel(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(channel_id): Path<String>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    proxy_to_python(
        &state,
        reqwest::Method::DELETE,
        &format!("/api/v1/telegram/channels/{}", urlencoding::encode(&channel_id)),
        &headers,
        None,
        None,
    )
    .await
}

/// PATCH /api/v1/telegram/channels/{channel_id}
pub async fn update_telegram_channel(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(channel_id): Path<String>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    proxy_to_python(
        &state,
        reqwest::Method::PATCH,
        &format!("/api/v1/telegram/channels/{}", urlencoding::encode(&channel_id)),
        &headers,
        Some(body),
        None,
    )
    .await
}

/// POST /api/v1/telegram/validate
pub async fn validate_telegram_channel(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    // No auth required; uses bot token from config
    let Some(ref bot_token) = state.config.telegram_bot_token else {
        return (
            StatusCode::OK,
            Json(serde_json::json!({
                "success": false,
                "message": "Telegram bot token not configured",
            })),
        )
            .into_response();
    };

    let chat_id = body
        .get("chat_id")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .or_else(|| {
            body.get("username")
                .and_then(|v| v.as_str())
                .map(|u| format!("@{u}"))
        })
        .or_else(|| body.get("id").and_then(|v| v.as_str()).map(str::to_string));

    let Some(chat_id_val) = chat_id else {
        return bad_request("Channel id, username or chat_id required");
    };

    let url = format!("https://api.telegram.org/bot{bot_token}/getChat");
    let resp = state
        .http
        .post(&url)
        .json(&serde_json::json!({"chat_id": chat_id_val}))
        .send()
        .await;

    match resp {
        Ok(r) => {
            let data: serde_json::Value = r.json().await.unwrap_or(serde_json::json!({}));
            if data["ok"].as_bool() == Some(true) {
                let result = &data["result"];
                let chat_type = result["type"].as_str().unwrap_or("");
                (
                    StatusCode::OK,
                    Json(serde_json::json!({
                        "success": true,
                        "message": "Channel is accessible",
                        "title": result["title"],
                        "username": result["username"],
                        "chat_id": result["id"].to_string(),
                        "member_count": result["member_count"],
                        "is_channel": chat_type == "channel",
                        "is_group": chat_type == "group" || chat_type == "supergroup",
                    })),
                )
                    .into_response()
            } else {
                let error_desc = data["description"]
                    .as_str()
                    .unwrap_or("Unknown error")
                    .to_string();
                (
                    StatusCode::OK,
                    Json(serde_json::json!({
                        "success": false,
                        "message": format!("Channel validation failed: {error_desc}"),
                    })),
                )
                    .into_response()
            }
        }
        Err(e) => {
            tracing::error!("validate_telegram_channel request error: {e}");
            (
                StatusCode::OK,
                Json(serde_json::json!({
                    "success": false,
                    "message": "Network error connecting to Telegram API",
                })),
            )
                .into_response()
        }
    }
}

// ─── Telegram account linking ─────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct TelegramLoginQuery {
    pub token: String,
    #[serde(default)]
    pub replace_existing: bool,
}

/// GET /api/v1/telegram/login
pub async fn telegram_login(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<TelegramLoginQuery>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    // Account linking requires Python-side bot state (pending tokens map)
    let q = format!(
        "token={}&replace_existing={}",
        urlencoding::encode(&params.token),
        params.replace_existing
    );
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/telegram/login",
        &headers,
        None,
        Some(&q),
    )
    .await
}

/// DELETE /api/v1/telegram/unlink
pub async fn telegram_unlink(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };
    proxy_to_python(
        &state,
        reqwest::Method::DELETE,
        "/api/v1/telegram/unlink",
        &headers,
        None,
        None,
    )
    .await
}
