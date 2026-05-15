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
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use crate::{
    jobs::enqueue::{enqueue_simple, EnqueueOpts},
    state::AppState,
};

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
        let default: Option<(i32,)> = sqlx::query_as(
            "SELECT id FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1",
        )
        .bind(user_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or_default();
        match default {
            Some((id,)) => id,
            None => return not_found("No default profile found"),
        }
    };

    let rows: Vec<IntegrationRow> = match sqlx::query_as(
        r#"SELECT id, profile_id, platform::text, is_enabled, sync_direction, scrobble_enabled,
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
    let mut map: std::collections::HashMap<String, &IntegrationRow> =
        std::collections::HashMap::new();
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
    match platform.as_str() {
        "trakt" => {
            let cid = params
                .client_id
                .or_else(|| state.config.trakt_client_id.clone());
            let Some(client_id) = cid else {
                return bad_request("client_id is required for this platform");
            };
            let auth_url = format!(
                "https://trakt.tv/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri=urn:ietf:wg:oauth:2.0:oob"
            );
            (
                StatusCode::OK,
                Json(serde_json::json!({"auth_url": auth_url, "platform": "trakt"})),
            )
                .into_response()
        }
        "simkl" => {
            let cid = params
                .client_id
                .or_else(|| state.config.simkl_client_id.clone());
            let Some(client_id) = cid else {
                return bad_request("client_id is required for this platform");
            };
            let redirect_uri = format!(
                "{}/api/v1/integrations/simkl/callback",
                state.config.host_url
            );
            let encoded_redirect = urlencoding::encode(&redirect_uri);
            let auth_url = format!(
                "https://simkl.com/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={encoded_redirect}"
            );
            (
                StatusCode::OK,
                Json(serde_json::json!({"auth_url": auth_url, "platform": "simkl"})),
            )
                .into_response()
        }
        _ => bad_request("OAuth not supported for this platform"),
    }
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
        query_parts
            .push("simkl_error_description=Missing+authorization+code+in+callback.".to_string());
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
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let profile_id = match params.profile_id {
        Some(pid) => pid,
        None => return bad_request("profile_id is required"),
    };

    // Verify user owns the profile
    let owns: Option<(i32,)> =
        match sqlx::query_as("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
            .bind(profile_id)
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => return db_error("connect_trakt profile check", &e),
        };
    if owns.is_none() {
        return not_found("Profile not found");
    }

    // Resolve client_id and client_secret
    let client_id = body
        .client_id
        .clone()
        .or_else(|| state.config.trakt_client_id.clone())
        .unwrap_or_default();
    let client_secret = body
        .client_secret
        .clone()
        .or_else(|| state.config.trakt_client_secret.clone())
        .unwrap_or_default();

    // Exchange code for token
    let token_resp = state
        .http
        .post("https://api.trakt.tv/oauth/token")
        .json(&serde_json::json!({
            "code": body.code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "authorization_code",
        }))
        .send()
        .await;

    let token_data = match token_resp {
        Ok(r) if r.status().is_success() => r
            .json::<serde_json::Value>()
            .await
            .unwrap_or(serde_json::json!({})),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": "Failed to connect Trakt. Invalid or expired code."})),
            )
                .into_response();
        }
    };

    let access_token = token_data["access_token"]
        .as_str()
        .unwrap_or("")
        .to_string();
    let refresh_token = token_data["refresh_token"]
        .as_str()
        .unwrap_or("")
        .to_string();

    let secrets = serde_json::json!({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    });

    let encrypted = crate::crypto::profile::encrypt_secrets(&secrets, &state.config.secret_key);
    let Some(encrypted_credentials) = encrypted else {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": "Failed to encrypt credentials"})),
        )
            .into_response();
    };

    if let Err(e) = sqlx::query(
        r#"INSERT INTO profile_integration (profile_id, platform, encrypted_credentials, is_enabled, sync_direction, scrobble_enabled)
           VALUES ($1, 'trakt', $2, true, 'two_way', true)
           ON CONFLICT (profile_id, platform) DO UPDATE SET encrypted_credentials = EXCLUDED.encrypted_credentials, is_enabled = true"#,
    )
    .bind(profile_id)
    .bind(&encrypted_credentials)
    .execute(&state.pool)
    .await
    {
        return db_error("connect_trakt upsert", &e);
    }

    (
        StatusCode::OK,
        Json(serde_json::json!({"message": "Trakt connected successfully", "platform": "trakt"})),
    )
        .into_response()
}

/// POST /api/v1/integrations/simkl/connect?profile_id=N
pub async fn connect_simkl(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<ProfileIdQuery>,
    Json(body): Json<SimklConnectRequest>,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let profile_id = match params.profile_id {
        Some(pid) => pid,
        None => return bad_request("profile_id is required"),
    };

    // Verify user owns the profile
    let owns: Option<(i32,)> =
        match sqlx::query_as("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
            .bind(profile_id)
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => return db_error("connect_simkl profile check", &e),
        };
    if owns.is_none() {
        return not_found("Profile not found");
    }

    // Resolve client_id and client_secret
    let client_id = body
        .client_id
        .clone()
        .or_else(|| state.config.simkl_client_id.clone())
        .unwrap_or_default();
    let client_secret = body
        .client_secret
        .clone()
        .or_else(|| state.config.simkl_client_secret.clone())
        .unwrap_or_default();

    let redirect_uri = format!(
        "{}/api/v1/integrations/simkl/callback",
        state.config.host_url
    );

    // Exchange code for token
    let token_resp = state
        .http
        .post("https://api.simkl.com/oauth/token")
        .header("simkl-api-key", &client_id)
        .json(&serde_json::json!({
            "code": body.code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }))
        .send()
        .await;

    let token_data = match token_resp {
        Ok(r) if r.status().is_success() => r
            .json::<serde_json::Value>()
            .await
            .unwrap_or(serde_json::json!({})),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": "Failed to connect Simkl. Invalid or expired code."})),
            )
                .into_response();
        }
    };

    let access_token = token_data["access_token"]
        .as_str()
        .unwrap_or("")
        .to_string();
    let refresh_token = token_data["refresh_token"]
        .as_str()
        .unwrap_or("")
        .to_string();

    let secrets = serde_json::json!({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    });

    let encrypted = crate::crypto::profile::encrypt_secrets(&secrets, &state.config.secret_key);
    let Some(encrypted_credentials) = encrypted else {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": "Failed to encrypt credentials"})),
        )
            .into_response();
    };

    if let Err(e) = sqlx::query(
        r#"INSERT INTO profile_integration (profile_id, platform, encrypted_credentials, is_enabled, sync_direction, scrobble_enabled)
           VALUES ($1, 'simkl', $2, true, 'two_way', false)
           ON CONFLICT (profile_id, platform) DO UPDATE SET encrypted_credentials = EXCLUDED.encrypted_credentials, is_enabled = true"#,
    )
    .bind(profile_id)
    .bind(&encrypted_credentials)
    .execute(&state.pool)
    .await
    {
        return db_error("connect_simkl upsert", &e);
    }

    (
        StatusCode::OK,
        Json(serde_json::json!({"message": "Simkl connected successfully", "platform": "simkl"})),
    )
        .into_response()
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

    let Some((integ_id, _existing_settings)) = row else {
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
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let profile_id = match params.profile_id {
        Some(pid) => pid,
        None => return bad_request("profile_id is required"),
    };

    let owns: Option<(i32,)> =
        match sqlx::query_as("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
            .bind(profile_id)
            .bind(user_id)
            .fetch_optional(&state.pool_ro)
            .await
        {
            Ok(r) => r,
            Err(e) => return db_error("trigger_sync profile check", &e),
        };
    if owns.is_none() {
        return not_found("Profile not found");
    }

    let row: Option<(i32,)> = match sqlx::query_as(
        "SELECT id FROM profile_integration WHERE profile_id = $1 AND platform = $2 AND is_enabled = true",
    )
    .bind(profile_id)
    .bind(&platform)
    .fetch_optional(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => return db_error("trigger_sync integration fetch", &e),
    };

    let Some((integ_id,)) = row else {
        return not_found("Integration not connected or disabled");
    };

    let payload = serde_json::json!({"integration_id": integ_id});
    let _ = enqueue_simple(
        &state.pool,
        "integration_syncs",
        &payload,
        EnqueueOpts::default(),
    )
    .await;

    (
        StatusCode::ACCEPTED,
        Json(serde_json::json!({
            "status": "accepted",
            "message": "Sync has been triggered. Results will appear shortly."
        })),
    )
        .into_response()
}

/// POST /api/v1/integrations/sync-all?profile_id=N
pub async fn trigger_sync_all(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(_params): Query<ProfileIdQuery>,
) -> Response {
    let Some(_user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let _ = enqueue_simple(
        &state.pool,
        "integration_syncs",
        &serde_json::json!({}),
        EnqueueOpts::default(),
    )
    .await;

    (
        StatusCode::ACCEPTED,
        Json(serde_json::json!({
            "status": "accepted",
            "message": "All syncs have been triggered. Results will appear shortly."
        })),
    )
        .into_response()
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
        format!("Telegram scraping is enabled with {global_channels_count} global channel(s)")
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

/// Helper: load `tgc` sub-object from default profile config (read-only pool).
/// Returns `(tgc_value, full_config_value)` — full_config is needed for merging on updates.
async fn load_profile_tgc(
    state: &AppState,
    user_id: i32,
) -> Result<(serde_json::Value, serde_json::Value), Response> {
    let row: Option<(Option<serde_json::Value>,)> = sqlx::query_as(
        "SELECT config FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1",
    )
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await
    .map_err(|e| db_error("load_profile_tgc", &e))?;

    let full_config = row
        .and_then(|(v,)| v)
        .unwrap_or_else(|| serde_json::json!({}));

    let tgc = full_config
        .get("tgc")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));

    Ok((tgc, full_config))
}

/// Helper: build the standard telegram-config response JSON.
fn build_tgc_response(
    tgc: &serde_json::Value,
    state: &AppState,
    telegram_user_id: Option<String>,
    linked_at: Option<DateTime<Utc>>,
) -> serde_json::Value {
    let enabled = tgc
        .get("enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let use_global = tgc
        .get("use_global_channels")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    let channels = tgc
        .get("ch")
        .cloned()
        .unwrap_or_else(|| serde_json::json!([]));
    let global_count = state.config.telegram_scraping_channels.len();

    serde_json::json!({
        "enabled": enabled,
        "channels": channels,
        "use_global_channels": use_global,
        "global_channels_available": global_count > 0,
        "global_channel_count": global_count,
        "account_linked": telegram_user_id.is_some(),
        "telegram_user_id": telegram_user_id,
        "linked_at": linked_at,
    })
}

/// GET /api/v1/telegram/config
pub async fn get_telegram_config(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let (tgc, _) = match load_profile_tgc(&state, user_id).await {
        Ok(v) => v,
        Err(r) => return r,
    };

    // Fetch telegram_user_id and linked_at from users table
    type UserTgRow = (Option<String>, Option<DateTime<Utc>>);
    let user_row: Option<UserTgRow> = match sqlx::query_as(
        "SELECT telegram_user_id::text, telegram_linked_at FROM users WHERE id = $1",
    )
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => return db_error("get_telegram_config users fetch", &e),
    };

    let (tg_uid, linked_at) = user_row.unwrap_or((None, None));
    (
        StatusCode::OK,
        Json(build_tgc_response(&tgc, &state, tg_uid, linked_at)),
    )
        .into_response()
}

/// PATCH /api/v1/telegram/config
pub async fn update_telegram_config(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let (mut tgc, mut full_config) = match load_profile_tgc(&state, user_id).await {
        Ok(v) => v,
        Err(r) => return r,
    };

    // Apply updates from body
    if let Some(enabled) = body.get("enabled").and_then(|v| v.as_bool()) {
        tgc["enabled"] = serde_json::json!(enabled);
    }
    if let Some(use_global) = body.get("use_global_channels").and_then(|v| v.as_bool()) {
        tgc["use_global_channels"] = serde_json::json!(use_global);
    }

    full_config["tgc"] = tgc.clone();

    if let Err(e) =
        sqlx::query("UPDATE user_profiles SET config = $1 WHERE user_id = $2 AND is_default = true")
            .bind(&full_config)
            .bind(user_id)
            .execute(&state.pool)
            .await
    {
        return db_error("update_telegram_config update", &e);
    }

    // Fetch user telegram link info for response
    type UserTgRow = (Option<String>, Option<DateTime<Utc>>);
    let user_row: Option<UserTgRow> = match sqlx::query_as(
        "SELECT telegram_user_id::text, telegram_linked_at FROM users WHERE id = $1",
    )
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => return db_error("update_telegram_config users fetch", &e),
    };

    let (tg_uid, linked_at) = user_row.unwrap_or((None, None));
    (
        StatusCode::OK,
        Json(build_tgc_response(&tgc, &state, tg_uid, linked_at)),
    )
        .into_response()
}

/// POST /api/v1/telegram/channels
pub async fn add_telegram_channel(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let channel_id = match body.get("id").and_then(|v| v.as_str()) {
        Some(s) => s.to_string(),
        None => return bad_request("Channel 'id' is required"),
    };

    let (tgc, _) = match load_profile_tgc(&state, user_id).await {
        Ok(v) => v,
        Err(r) => return r,
    };

    let mut channels: Vec<serde_json::Value> = tgc
        .get("ch")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    // Check for duplicate
    if channels
        .iter()
        .any(|ch| ch.get("id").and_then(|v| v.as_str()) == Some(&channel_id))
    {
        return (
            StatusCode::CONFLICT,
            Json(serde_json::json!({"error": "Channel already exists"})),
        )
            .into_response();
    }

    let new_channel = serde_json::json!({
        "id": channel_id,
        "name": body.get("name").and_then(|v| v.as_str()).unwrap_or(&channel_id),
        "enabled": body.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
        "priority": body.get("priority").and_then(|v| v.as_i64()).unwrap_or(1),
    });
    channels.push(new_channel.clone());

    let channels_json = serde_json::to_string(&channels).unwrap_or_else(|_| "[]".to_string());

    if let Err(e) = sqlx::query(
        "UPDATE user_profiles SET config = jsonb_set(COALESCE(config, '{}'), ARRAY['tgc','ch'], $1::jsonb, true) WHERE user_id = $2 AND is_default = true",
    )
    .bind(&channels_json)
    .bind(user_id)
    .execute(&state.pool)
    .await
    {
        return db_error("add_telegram_channel update", &e);
    }

    (StatusCode::CREATED, Json(new_channel)).into_response()
}

/// DELETE /api/v1/telegram/channels/{channel_id}
pub async fn remove_telegram_channel(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(channel_id): Path<String>,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let (tgc, _) = match load_profile_tgc(&state, user_id).await {
        Ok(v) => v,
        Err(r) => return r,
    };

    let channels: Vec<serde_json::Value> = tgc
        .get("ch")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let original_len = channels.len();
    let updated: Vec<serde_json::Value> = channels
        .into_iter()
        .filter(|ch| ch.get("id").and_then(|v| v.as_str()) != Some(&channel_id))
        .collect();

    if updated.len() == original_len {
        return not_found("Channel not found");
    }

    let channels_json = serde_json::to_string(&updated).unwrap_or_else(|_| "[]".to_string());

    if let Err(e) = sqlx::query(
        "UPDATE user_profiles SET config = jsonb_set(COALESCE(config, '{}'), ARRAY['tgc','ch'], $1::jsonb, true) WHERE user_id = $2 AND is_default = true",
    )
    .bind(&channels_json)
    .bind(user_id)
    .execute(&state.pool)
    .await
    {
        return db_error("remove_telegram_channel update", &e);
    }

    StatusCode::NO_CONTENT.into_response()
}

/// PATCH /api/v1/telegram/channels/{channel_id}
pub async fn update_telegram_channel(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(channel_id): Path<String>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    let (tgc, _) = match load_profile_tgc(&state, user_id).await {
        Ok(v) => v,
        Err(r) => return r,
    };

    let mut channels: Vec<serde_json::Value> = tgc
        .get("ch")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let pos = channels
        .iter()
        .position(|ch| ch.get("id").and_then(|v| v.as_str()) == Some(&channel_id));

    let Some(idx) = pos else {
        return not_found("Channel not found");
    };

    // Apply partial updates
    if let Some(name) = body.get("name").and_then(|v| v.as_str()) {
        channels[idx]["name"] = serde_json::json!(name);
    }
    if let Some(enabled) = body.get("enabled").and_then(|v| v.as_bool()) {
        channels[idx]["enabled"] = serde_json::json!(enabled);
    }
    if let Some(priority) = body.get("priority").and_then(|v| v.as_i64()) {
        channels[idx]["priority"] = serde_json::json!(priority);
    }

    let updated_channel = channels[idx].clone();
    let channels_json = serde_json::to_string(&channels).unwrap_or_else(|_| "[]".to_string());

    if let Err(e) = sqlx::query(
        "UPDATE user_profiles SET config = jsonb_set(COALESCE(config, '{}'), ARRAY['tgc','ch'], $1::jsonb, true) WHERE user_id = $2 AND is_default = true",
    )
    .bind(&channels_json)
    .bind(user_id)
    .execute(&state.pool)
    .await
    {
        return db_error("update_telegram_channel update", &e);
    }

    (StatusCode::OK, Json(updated_channel)).into_response()
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
    use fred::prelude::{Expiration, KeysInterface};

    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    if state.config.telegram_bot_token.is_none() {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"detail": "Telegram bot not configured"})),
        )
            .into_response();
    }

    // Look up login token stored by the Telegram bot
    let token_key = format!("telegram:login_token:{}", params.token);
    let raw: Option<String> = match state.redis.get(&token_key).await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("telegram_login redis get: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"detail": "Internal error"})),
            )
                .into_response();
        }
    };

    let Some(raw) = raw else {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "message": "Invalid or expired login token",
                "requires_confirmation": false
            })),
        )
            .into_response();
    };

    let login_data: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Invalid token data"})),
            )
                .into_response();
        }
    };

    // telegram_user_id may be integer or string in the stored JSON
    let telegram_user_id = match login_data["telegram_user_id"]
        .as_i64()
        .map(|v| v.to_string())
        .or_else(|| login_data["telegram_user_id"].as_str().map(str::to_string))
    {
        Some(v) => v,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Invalid token data: missing telegram_user_id"})),
            )
                .into_response();
        }
    };

    // Remember old mapping so we can remove a stale cache entry if the user switches accounts
    let current_telegram_id: Option<String> =
        sqlx::query_scalar("SELECT telegram_user_id FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
            .flatten();

    // Check for a conflict: another user already owns this Telegram account
    let conflicting_user_id: Option<i32> =
        sqlx::query_scalar("SELECT id FROM users WHERE telegram_user_id = $1 AND id != $2 LIMIT 1")
            .bind(&telegram_user_id)
            .bind(user_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    if conflicting_user_id.is_some() && !params.replace_existing {
        return (
            StatusCode::OK,
            Json(serde_json::json!({
                "success": false,
                "message": "This Telegram account is already linked to another MediaFusion account. Do you want to replace the existing link and continue?",
                "requires_confirmation": true
            })),
        )
            .into_response();
    }

    // Clear the conflicting user's link before taking ownership
    if let Some(conflicting_id) = conflicting_user_id {
        if let Err(e) = sqlx::query(
            "UPDATE users SET telegram_user_id = NULL, telegram_linked_at = NULL WHERE id = $1",
        )
        .bind(conflicting_id)
        .execute(&state.pool)
        .await
        {
            tracing::warn!("telegram_login clear conflict user {conflicting_id}: {e}");
        }
    }

    // Link the Telegram account to the authenticated user
    if let Err(e) = sqlx::query(
        "UPDATE users SET telegram_user_id = $1, telegram_linked_at = NOW() WHERE id = $2",
    )
    .bind(&telegram_user_id)
    .bind(user_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("telegram_login update user {user_id}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"detail": "Failed to link Telegram account"})),
        )
            .into_response();
    }

    // Refresh the user-mapping cache entry (1-hour TTL, shared with the Python bot)
    let mapping_key = format!("telegram:user_mapping:{telegram_user_id}");
    if let Err(e) = state
        .redis
        .set::<String, _, _>(
            &mapping_key,
            user_id.to_string().as_str(),
            Some(Expiration::EX(3600)),
            None,
            false,
        )
        .await
    {
        tracing::debug!("telegram_login set user mapping: {e}");
    }

    // Remove the stale cache entry if the user was previously linked to a different account
    if let Some(old_tg_id) = &current_telegram_id {
        if old_tg_id != &telegram_user_id {
            let stale_key = format!("telegram:user_mapping:{old_tg_id}");
            let _: Result<i64, _> = state.redis.del(&stale_key).await;
        }
    }

    // Consume the one-time login token
    let _: Result<i64, _> = state.redis.del(&token_key).await;

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "success": true,
            "message": "✅ Telegram account linked successfully!\n\nYour uploaded content will now be stored with your MediaFusion account.",
            "requires_confirmation": false
        })),
    )
        .into_response()
}

/// DELETE /api/v1/telegram/unlink
pub async fn telegram_unlink(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    use fred::prelude::KeysInterface;

    let Some(user_id) = validate_token(&headers, &state.config.secret_key_raw) else {
        return unauthorized();
    };

    // Capture the telegram_user_id before clearing so we can remove the Redis cache
    let telegram_user_id: Option<String> =
        sqlx::query_scalar("SELECT telegram_user_id FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
            .flatten();

    if let Err(e) = sqlx::query(
        "UPDATE users SET telegram_user_id = NULL, telegram_linked_at = NULL WHERE id = $1",
    )
    .bind(user_id)
    .execute(&state.pool)
    .await
    {
        return db_error("telegram_unlink update", &e);
    }

    // Remove the user-mapping cache entry shared with the Python bot
    if let Some(tg_id) = telegram_user_id {
        let mapping_key = format!("telegram:user_mapping:{tg_id}");
        let _: Result<i64, _> = state.redis.del(&mapping_key).await;
    }

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "success": true,
            "message": "Telegram account unlinked successfully.",
        })),
    )
        .into_response()
}
