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
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Redirect, Response},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::{
    db::{IntegrationType, types::UserId},
    jobs::{
        enqueue::{EnqueueOpts, enqueue_simple},
        handlers::integration_syncs::{SyncOptions, sync_integration_inline},
    },
    routes::auth_guard,
    state::AppState,
};

fn parse_integration_platform(platform: &str) -> Option<IntegrationType> {
    IntegrationType::from_wire(platform)
}

// ─── Auth helpers ─────────────────────────────────────────────────────────────

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
    IntegrationType,
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
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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

    // Build a map of platform → row (normalize to lowercase to match KNOWN_PLATFORMS)
    let mut map: std::collections::HashMap<String, &IntegrationRow> =
        std::collections::HashMap::new();
    for row in &rows {
        map.insert(row.2.as_wire().to_string(), row);
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
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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
    .bind(parse_integration_platform(&platform).unwrap_or(IntegrationType::Trakt))
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
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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

    let expires_at = token_data["created_at"]
        .as_i64()
        .or_else(|| Some(Utc::now().timestamp()))
        .zip(token_data["expires_in"].as_i64())
        .map(|(created, exp_in)| created + exp_in);

    let secrets = serde_json::json!({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "expires_at": expires_at,
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
        r#"INSERT INTO profile_integration (profile_id, platform, encrypted_credentials, is_enabled, sync_direction, scrobble_enabled, settings)
           VALUES ($1, 'TRAKT', $2, true, 'two_way', true, '{"min_watch_percent": 80}'::json)
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
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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

    let expires_at = token_data["expires_in"]
        .as_i64()
        .map(|exp_in| Utc::now().timestamp() + exp_in);

    let secrets = serde_json::json!({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "expires_at": expires_at,
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
           VALUES ($1, 'SIMKL', $2, true, 'two_way', false)
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
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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
    .bind(parse_integration_platform(&platform).unwrap_or(IntegrationType::Trakt))
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
            .bind(parse_integration_platform(&platform).unwrap_or(IntegrationType::Trakt))
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
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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
    .bind(parse_integration_platform(&platform).unwrap_or(IntegrationType::Trakt))
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
        let mut q = sqlx::query(sqlx::AssertSqlSafe(sql.as_str()));
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
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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
    .bind(parse_integration_platform(&platform).unwrap_or(IntegrationType::Trakt))
    .fetch_optional(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => return db_error("trigger_sync integration fetch", &e),
    };

    let Some((integ_id,)) = row else {
        return not_found("Integration not connected or disabled");
    };

    // Run sync synchronously for this single integration so the caller gets the
    // actual result. The background worker handles the scheduled sweep across
    // all users; this endpoint is the per-user "Sync Now" action.
    sync_integration_inline(
        Arc::clone(&state),
        integ_id,
        SyncOptions {
            direction: params.direction.clone(),
            full_sync: params.full_sync,
        },
    )
    .await;

    // Return the updated status row so the UI can display the result directly.
    type StatusRow = (
        Option<String>,
        Option<DateTime<Utc>>,
        Option<String>,
        Option<serde_json::Value>,
    );
    let updated: Option<StatusRow> = sqlx::query_as(
        r#"SELECT last_sync_status, last_sync_at, last_sync_error, last_sync_stats
           FROM profile_integration WHERE id = $1"#,
    )
    .bind(integ_id)
    .fetch_optional(&state.pool_ro)
    .await
    .ok()
    .flatten();

    let (status_opt, last_sync_at, last_sync_error, last_sync_stats) = updated.unwrap_or((
        None,
        None,
        Some("integration row not found after sync".to_string()),
        None,
    ));
    let status = status_opt.unwrap_or_else(|| "unknown".to_string());

    let http_status = if status == "success" || status == "partial" {
        StatusCode::OK
    } else {
        StatusCode::BAD_GATEWAY
    };

    (
        http_status,
        Json(serde_json::json!({
            "status": status,
            "platform": platform,
            "last_sync_at": last_sync_at,
            "last_sync_error": last_sync_error,
            "last_sync_stats": last_sync_stats,
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
    let Some(_user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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
    let api_configured = state.config.telegram_api_id.is_some();
    let bot_configured = state.config.telegram_bot_token.is_some();
    let scraping_available = state.telegram_clients.api_configured();

    let message = if !api_configured {
        "Telegram API credentials are not configured".to_string()
    } else if !scraping_available {
        "Telegram API credentials are incomplete".to_string()
    } else {
        "Users can connect their own Telegram account for channel scraping".to_string()
    };

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "scraper_enabled": scraping_available,
            "bot_configured": bot_configured,
            "api_credentials_configured": api_configured,
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
async fn build_tgc_response(
    state: &AppState,
    user_id: UserId,
    tgc: &serde_json::Value,
    session_connected: bool,
    session_account_id: Option<i64>,
    telegram_user_id: Option<String>,
    linked_at: Option<DateTime<Utc>>,
) -> serde_json::Value {
    let enabled = tgc
        .get("enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let channel_rows: Vec<crate::db::telegram_channels::UserChannel> =
        crate::db::telegram_channels::list_user_channels(&state.pool_ro, user_id).await;
    let stats =
        crate::db::telegram_channels::scrape_stats_for_channels(&state.pool_ro, &channel_rows)
            .await;

    let channels: Vec<serde_json::Value> = channel_rows
        .into_iter()
        .map(|ch| {
            let stream_count = stats.get(&ch.id).map(|s| s.stream_count).unwrap_or(0);
            let is_public = crate::util::telegram_channel_id::is_public_username(&ch.id);
            serde_json::json!({
                "id": ch.id,
                "name": ch.name,
                "enabled": ch.enabled,
                "priority": 1,
                "is_public": is_public,
                "stream_count": stream_count,
            })
        })
        .collect();

    serde_json::json!({
        "enabled": enabled,
        "channels": channels,
        "account_linked": telegram_user_id.is_some(),
        "telegram_user_id": telegram_user_id,
        "linked_at": linked_at,
        "session_connected": session_connected,
        "session_telegram_account_id": session_account_id,
    })
}

/// GET /api/v1/telegram/config
pub async fn get_telegram_config(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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
    let session_row =
        crate::db::user_telegram_session::get_session(&state.pool_ro, UserId(user_id)).await;
    let session_connected = session_row.is_some();
    let session_account_id = session_row.map(|r| r.telegram_account_id);
    (
        StatusCode::OK,
        Json(
            build_tgc_response(
                &state,
                UserId(user_id),
                &tgc,
                session_connected,
                session_account_id,
                tg_uid,
                linked_at,
            )
            .await,
        ),
    )
        .into_response()
}

/// PATCH /api/v1/telegram/config
pub async fn update_telegram_config(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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
    let session_row =
        crate::db::user_telegram_session::get_session(&state.pool_ro, UserId(user_id)).await;
    let session_connected = session_row.is_some();
    let session_account_id = session_row.map(|r| r.telegram_account_id);
    (
        StatusCode::OK,
        Json(
            build_tgc_response(
                &state,
                UserId(user_id),
                &tgc,
                session_connected,
                session_account_id,
                tg_uid,
                linked_at,
            )
            .await,
        ),
    )
        .into_response()
}

/// POST /api/v1/telegram/channels
pub async fn add_telegram_channel(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    let channel_id = match body.get("id").and_then(|v| v.as_str()) {
        Some(s) => s,
        None => return bad_request("Channel 'id' is required"),
    };
    let name = body.get("name").and_then(|v| v.as_str());

    match crate::db::telegram_channels::add_user_channel(
        &state.pool,
        UserId(user_id),
        channel_id,
        name,
    )
    .await
    {
        Ok(ch) => {
            let stats = crate::db::telegram_channels::scrape_stats_for_channels(
                &state.pool_ro,
                std::slice::from_ref(&ch),
            )
            .await;
            let stream_count = stats.get(&ch.id).map(|s| s.stream_count).unwrap_or(0);
            (
                StatusCode::CREATED,
                Json(serde_json::json!({
                    "id": ch.id,
                    "name": ch.name,
                    "enabled": ch.enabled,
                    "priority": 1,
                    "is_public": crate::util::telegram_channel_id::is_public_username(&ch.id),
                    "stream_count": stream_count,
                })),
            )
                .into_response()
        }
        Err(crate::db::telegram_channels::ChannelMutationError::Duplicate) => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({"error": "Channel already exists"})),
        )
            .into_response(),
        Err(crate::db::telegram_channels::ChannelMutationError::NotFound) => {
            bad_request("Invalid channel identifier")
        }
        Err(crate::db::telegram_channels::ChannelMutationError::Database(e)) => {
            db_error("add_telegram_channel update", &e)
        }
    }
}

/// DELETE /api/v1/telegram/channels/{channel_id}
pub async fn remove_telegram_channel(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(channel_id): Path<String>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    match crate::db::telegram_channels::remove_user_channel(
        &state.pool,
        UserId(user_id),
        &channel_id,
    )
    .await
    {
        Ok(true) => StatusCode::NO_CONTENT.into_response(),
        Ok(false) => not_found("Channel not found"),
        Err(e) => db_error("remove_telegram_channel update", &e),
    }
}

/// PATCH /api/v1/telegram/channels/{channel_id}
pub async fn update_telegram_channel(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(channel_id): Path<String>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    match crate::db::telegram_channels::update_user_channel(
        &state.pool,
        UserId(user_id),
        &channel_id,
        body.get("name").and_then(|v| v.as_str()),
        body.get("enabled").and_then(|v| v.as_bool()),
        body.get("priority").and_then(|v| v.as_i64()),
    )
    .await
    {
        Ok(Some(updated)) => (StatusCode::OK, Json(updated)).into_response(),
        Ok(None) => not_found("Channel not found"),
        Err(e) => db_error("update_telegram_channel update", &e),
    }
}

#[derive(Debug, Deserialize)]
pub struct TelegramDialogsQuery {
    pub limit: Option<usize>,
}

/// GET /api/v1/telegram/dialogs
pub async fn list_telegram_dialogs(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(query): Query<TelegramDialogsQuery>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    if !crate::db::user_telegram_session::has_session(&state.pool_ro, UserId(user_id)).await {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "Telegram scraping session is not connected",
            })),
        )
            .into_response();
    }

    let limit = query.limit.unwrap_or(60).clamp(1, 200);
    match crate::services::telegram_dialogs::list_scrapable_dialogs(
        &state.pool,
        &state.telegram_clients,
        UserId(user_id),
        limit,
    )
    .await
    {
        Ok(dialogs) => (
            StatusCode::OK,
            Json(serde_json::json!({ "dialogs": dialogs })),
        )
            .into_response(),
        Err(message) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": message })),
        )
            .into_response(),
    }
}

/// GET /api/v1/telegram/dialogs/{channel_id}/photo
pub async fn get_telegram_dialog_photo(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(channel_id): Path<String>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    let user_id = UserId(user_id);
    let channel_id_for_lookup = channel_id.clone();

    let bytes = state
        .telegram_clients
        .with_user_client(&state.pool, user_id, |client| {
            let channel_id = channel_id_for_lookup.clone();
            async move {
                let mut dialog_peers =
                    crate::services::telegram_peer::cached_dialog_peer_map(user_id, &client).await;
                let mut bytes = crate::services::telegram_peer::download_channel_photo(
                    &client,
                    &channel_id,
                    &dialog_peers,
                )
                .await;
                if bytes.is_none() {
                    dialog_peers = crate::services::telegram_peer::load_dialog_peer_map(&client)
                        .await
                        .0;
                    crate::services::telegram_peer::store_dialog_peer_map(
                        user_id,
                        dialog_peers.clone(),
                    )
                    .await;
                    bytes = crate::services::telegram_peer::download_channel_photo(
                        &client,
                        &channel_id,
                        &dialog_peers,
                    )
                    .await;
                }
                bytes
            }
        })
        .await
        .ok()
        .flatten();

    let Some(bytes) = bytes else {
        return StatusCode::NOT_FOUND.into_response();
    };

    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, "image/jpeg"),
            (header::CACHE_CONTROL, "private, max-age=3600"),
        ],
        bytes,
    )
        .into_response()
}

#[derive(Debug, Deserialize)]
pub struct TriggerTelegramScrapeBody {
    pub channel: Option<String>,
    pub scrape_all: Option<bool>,
    pub message_limit: Option<i32>,
    pub scrape_all_messages: Option<bool>,
    pub channel_limits: Option<serde_json::Value>,
}

/// POST /api/v1/telegram/scrape
pub async fn trigger_telegram_scrape(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<TriggerTelegramScrapeBody>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    if !crate::db::user_telegram_session::has_session(&state.pool_ro, UserId(user_id)).await {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "Telegram scraping session is not connected",
            })),
        )
            .into_response();
    }

    let scrape_all = body.scrape_all.unwrap_or(body.channel.is_none());
    let channels = if scrape_all {
        crate::db::telegram_channels::user_scraping_channels(&state.pool_ro, UserId(user_id)).await
    } else {
        body.channel
            .as_deref()
            .map(|c| vec![crate::util::telegram_channel_id::normalize_stored_channel_id(c)])
            .unwrap_or_default()
            .into_iter()
            .filter(|s| !s.is_empty())
            .collect()
    };

    if channels.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "No scraping channels configured",
            })),
        )
            .into_response();
    }

    let mut payload = serde_json::json!({
        "mediafusion_user_id": user_id,
        "scrape_all": scrape_all,
        "scrape_all_messages": body.scrape_all_messages.unwrap_or(false),
    });
    if let Some(channel_limits) = body.channel_limits {
        payload["channel_limits"] = channel_limits;
    }
    if body.scrape_all_messages != Some(true) {
        payload["message_limit"] = serde_json::json!(
            body.message_limit
                .unwrap_or(crate::scrapers::telegram::DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT)
        );
    }
    if !scrape_all && let Some(channel) = channels.first() {
        payload["channel"] = serde_json::json!(channel);
    }

    match crate::jobs::enqueue_simple(
        &state.pool,
        "telegram_bg",
        &payload,
        crate::jobs::EnqueueOpts {
            dedupe_key: Some(format!("telegram_scrape_web:{user_id}")),
            ..Default::default()
        },
    )
    .await
    {
        Ok(Some(_)) => (
            StatusCode::ACCEPTED,
            Json(serde_json::json!({
                "status": "queued",
                "message": "Telegram scrape job queued. Results appear after the worker runs.",
                "channels": channels.len(),
            })),
        )
            .into_response(),
        Ok(None) => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({
                "detail": "A scrape job is already queued or running for your account",
                "error": "A scrape job is already queued or running for your account",
            })),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("trigger_telegram_scrape enqueue: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "Failed to queue scrape job" })),
            )
                .into_response()
        }
    }
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

    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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
    if let Some(conflicting_id) = conflicting_user_id
        && let Err(e) = sqlx::query(
            "UPDATE users SET telegram_user_id = NULL, telegram_linked_at = NULL WHERE id = $1",
        )
        .bind(conflicting_id)
        .execute(&state.pool)
        .await
    {
        tracing::warn!("telegram_login clear conflict user {conflicting_id}: {e}");
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
    let mapping_key = crate::bot::user_mapping_key(&telegram_user_id);
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
    if let Some(old_tg_id) = &current_telegram_id
        && old_tg_id != &telegram_user_id
    {
        let stale_key = crate::bot::user_mapping_key(old_tg_id);
        let _: Result<i64, _> = state.redis.del(&stale_key).await;
    }

    // Consume the one-time login token
    let _: Result<i64, _> = state.redis.del(&token_key).await;

    // Send a confirmation message to the user in Telegram (fire-and-forget).
    if let Ok(tg_chat_id) = telegram_user_id.parse::<i64>()
        && let Ok(api) = crate::bot::BotApi::from_state(&state)
    {
        let username_hint = sqlx::query_scalar::<_, Option<String>>(
            "SELECT NULLIF(username, '') FROM users WHERE id = $1",
        )
        .bind(user_id)
        .fetch_optional(&state.pool_ro)
        .await
        .ok()
        .flatten()
        .flatten();

        let greeting = match &username_hint {
            Some(name) => format!("*{}*", name),
            None => "your MediaFusion account".to_string(),
        };

        let msg = format!(
            "✅ *Account Linked Successfully!*\n\n\
                 Your Telegram account has been linked to {greeting}.\n\n\
                 You can now:\n\
                 • Forward content to the bot and it will be saved to your account\n\
                 • Use /status to verify your link at any time\n\
                 • Use /help to see all available commands"
        );
        tokio::spawn(async move {
            let _ = api.send_message(tg_chat_id, &msg, None).await;
        });
    }

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

    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
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
        let mapping_key = crate::bot::user_mapping_key(&tg_id);
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
