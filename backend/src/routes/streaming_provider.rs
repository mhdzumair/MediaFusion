/// Streaming provider debrid cache status and submission.
///
/// Routes:
///   POST /streaming_provider/cache/status
///   POST /streaming_provider/cache/submit
///
/// These mirror Python's cache_helpers: Redis hash `debrid_cache:{service}`
/// stores info_hash → unix expiry timestamp.
use std::sync::Arc;

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Redirect, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use fred::prelude::HashesInterface;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{state::AppState, util::http as http_util, util::retry};

const CACHE_KEY_PREFIX: &str = "debrid_cache:";
const EXPIRY_DAYS_SECS: i64 = 7 * 86400;

const REALDEBRID_CLIENT_ID: &str = "X245A4XAIBGVM";
const DEBRIDLINK_CLIENT_ID: &str = "RyrV22FOg30DsxjYPziRKA";

#[derive(Deserialize)]
pub struct CacheStatusRequest {
    pub service: String,
    pub info_hashes: Vec<String>,
}

#[derive(Serialize)]
pub struct CacheStatusResponse {
    pub cached_status: std::collections::HashMap<String, bool>,
}

#[derive(Deserialize)]
pub struct CacheSubmitRequest {
    pub service: String,
    pub info_hashes: Vec<String>,
}

#[derive(Serialize)]
pub struct CacheSubmitResponse {
    pub success: bool,
    pub message: String,
}

pub async fn check_cache_status(
    State(state): State<Arc<AppState>>,
    Json(req): Json<CacheStatusRequest>,
) -> impl IntoResponse {
    if req.info_hashes.is_empty() {
        return Json(CacheStatusResponse {
            cached_status: std::collections::HashMap::new(),
        });
    }

    let service = normalize_service(&req.service);
    let cache_key = format!("{CACHE_KEY_PREFIX}{service}");
    let now = Utc::now().timestamp();

    let fields: Vec<String> = req.info_hashes.clone();
    let timestamps: Vec<Option<String>> = state
        .redis
        .hmget(&cache_key, fields)
        .await
        .unwrap_or_else(|_| vec![None; req.info_hashes.len()]);

    let mut cached_status = std::collections::HashMap::new();
    let mut expired: Vec<String> = Vec::new();

    for (hash, ts_opt) in req.info_hashes.iter().zip(timestamps.iter()) {
        match ts_opt {
            Some(ts_str) => {
                let expiry: i64 = ts_str.parse().unwrap_or(0);
                if expiry > now {
                    cached_status.insert(hash.clone(), true);
                } else {
                    expired.push(hash.clone());
                    cached_status.insert(hash.clone(), false);
                }
            }
            None => {
                cached_status.insert(hash.clone(), false);
            }
        }
    }

    // Clean up expired entries (best-effort)
    if !expired.is_empty() {
        let _ = state.redis.hdel::<(), _, _>(&cache_key, expired).await;
    }

    Json(CacheStatusResponse { cached_status })
}

pub async fn submit_cached_hashes(
    State(state): State<Arc<AppState>>,
    Json(req): Json<CacheSubmitRequest>,
) -> impl IntoResponse {
    if req.info_hashes.is_empty() {
        return (
            StatusCode::OK,
            Json(CacheSubmitResponse {
                success: true,
                message: "No info hashes provided".into(),
            }),
        );
    }

    let service = normalize_service(&req.service);
    let cache_key = format!("{CACHE_KEY_PREFIX}{service}");
    let expiry_ts = (Utc::now().timestamp() + EXPIRY_DAYS_SECS).to_string();

    // Build mapping: info_hash → expiry timestamp
    let mapping: Vec<(String, String)> = req
        .info_hashes
        .iter()
        .map(|h| (h.clone(), expiry_ts.clone()))
        .collect();

    let result = state.redis.hset::<(), _, _>(&cache_key, mapping).await;

    match result {
        Ok(_) => (
            StatusCode::OK,
            Json(CacheSubmitResponse {
                success: true,
                message: format!(
                    "Stored {} cached info hashes for {}",
                    req.info_hashes.len(),
                    service
                ),
            }),
        ),
        Err(e) => {
            tracing::error!("submit_cached_hashes Redis error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(CacheSubmitResponse {
                    success: false,
                    message: "Error storing cached info hashes".into(),
                }),
            )
        }
    }
}

/// StremThru uses the store name as service name when set.
fn normalize_service(service: &str) -> &str {
    service
}

// ── Provider OAuth / device-code auth ────────────────────────────────────────

/// GET /streaming_provider/realdebrid/get-device-code
pub async fn realdebrid_get_device_code(State(state): State<Arc<AppState>>) -> Response {
    let url = format!(
        "https://api.real-debrid.com/oauth/v2/device/code?client_id={}&new_credentials=yes",
        REALDEBRID_CLIENT_ID
    );
    match retry::with_transport_retry("realdebrid_get_device_code", || state.http.get(&url).send())
        .await
    {
        Ok(resp) => {
            let status = resp.status();
            match resp.json::<Value>().await {
                Ok(body) => (
                    StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::OK),
                    Json(body),
                )
                    .into_response(),
                Err(e) => {
                    tracing::error!("realdebrid_get_device_code: parse error: {e}");
                    (
                        StatusCode::BAD_GATEWAY,
                        Json(serde_json::json!({"detail": "Invalid response from Real-Debrid"})),
                    )
                        .into_response()
                }
            }
        }
        Err(e) => {
            tracing::error!(
                error_kind = http_util::transport_error_kind(&e),
                root_cause = http_util::root_cause(&e),
                "realdebrid_get_device_code: request error: {e}"
            );
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({"detail": "Failed to contact Real-Debrid"})),
            )
                .into_response()
        }
    }
}

/// POST /streaming_provider/realdebrid/authorize
pub async fn realdebrid_authorize(
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> Response {
    let device_code = match body.get("device_code").and_then(|v| v.as_str()) {
        Some(c) => c.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(serde_json::json!({"detail": "Missing device_code"})),
            )
                .into_response();
        }
    };

    let url = format!(
        "https://api.real-debrid.com/oauth/v2/device/credentials?client_id={}&code={}",
        REALDEBRID_CLIENT_ID, device_code
    );
    match state.http.get(&url).send().await {
        Ok(resp) => {
            let status = resp.status();
            match resp.json::<Value>().await {
                Ok(json_body) => (
                    StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::OK),
                    Json(json_body),
                )
                    .into_response(),
                Err(e) => {
                    tracing::error!("realdebrid_authorize: parse error: {e}");
                    (
                        StatusCode::BAD_GATEWAY,
                        Json(serde_json::json!({"detail": "Invalid response from Real-Debrid"})),
                    )
                        .into_response()
                }
            }
        }
        Err(e) => {
            tracing::error!("realdebrid_authorize: request error: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({"detail": "Failed to contact Real-Debrid"})),
            )
                .into_response()
        }
    }
}

/// GET /streaming_provider/debridlink/get-device-code
pub async fn debridlink_get_device_code(State(state): State<Arc<AppState>>) -> Response {
    let url = "https://debrid-link.com/api/oauth/device/code";
    let payload = serde_json::json!({
        "client_id": DEBRIDLINK_CLIENT_ID,
        "scope": "get.post.downloader get.post.seedbox get.account get.files get.post.stream",
    });
    match state.http.post(url).json(&payload).send().await {
        Ok(resp) => {
            let status = resp.status();
            match resp.json::<Value>().await {
                Ok(body) => (
                    StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::OK),
                    Json(body),
                )
                    .into_response(),
                Err(e) => {
                    tracing::error!("debridlink_get_device_code: parse error: {e}");
                    (
                        StatusCode::BAD_GATEWAY,
                        Json(serde_json::json!({"detail": "Invalid response from DebridLink"})),
                    )
                        .into_response()
                }
            }
        }
        Err(e) => {
            tracing::error!("debridlink_get_device_code: request error: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({"detail": "Failed to contact DebridLink"})),
            )
                .into_response()
        }
    }
}

/// POST /streaming_provider/debridlink/authorize
pub async fn debridlink_authorize(
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> Response {
    let device_code = match body.get("device_code").and_then(|v| v.as_str()) {
        Some(c) => c.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(serde_json::json!({"detail": "Missing device_code"})),
            )
                .into_response();
        }
    };

    let url = "https://debrid-link.com/api/oauth/token";
    let payload = serde_json::json!({
        "client_id": DEBRIDLINK_CLIENT_ID,
        "code": device_code,
        "grant_type": "http://oauth.net/grant_type/device/1.0",
    });
    match state.http.post(url).json(&payload).send().await {
        Ok(resp) => {
            let status = resp.status();
            match resp.json::<Value>().await {
                Ok(json_body) => {
                    if status.is_success() {
                        // Extract refresh_token and base64-url-no-pad encode it
                        if let Some(refresh_token) =
                            json_body.get("refresh_token").and_then(|v| v.as_str())
                        {
                            let encoded = URL_SAFE_NO_PAD.encode(refresh_token.as_bytes());
                            (StatusCode::OK, Json(serde_json::json!({"token": encoded})))
                                .into_response()
                        } else {
                            tracing::warn!("debridlink_authorize: no refresh_token in response");
                            (
                                StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::OK),
                                Json(json_body),
                            )
                                .into_response()
                        }
                    } else {
                        (
                            StatusCode::from_u16(status.as_u16())
                                .unwrap_or(StatusCode::BAD_GATEWAY),
                            Json(json_body),
                        )
                            .into_response()
                    }
                }
                Err(e) => {
                    tracing::error!("debridlink_authorize: parse error: {e}");
                    (
                        StatusCode::BAD_GATEWAY,
                        Json(serde_json::json!({"detail": "Invalid response from DebridLink"})),
                    )
                        .into_response()
                }
            }
        }
        Err(e) => {
            tracing::error!("debridlink_authorize: request error: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({"detail": "Failed to contact DebridLink"})),
            )
                .into_response()
        }
    }
}

#[derive(Deserialize)]
pub struct PremiumizeAuthorizeQuery {
    pub state: Option<String>,
}

/// GET /streaming_provider/premiumize/authorize
pub async fn premiumize_authorize(
    State(state): State<Arc<AppState>>,
    Query(params): Query<PremiumizeAuthorizeQuery>,
) -> Response {
    let client_id = std::env::var("PREMIUMIZE_OAUTH_CLIENT_ID").unwrap_or_default();

    if client_id.is_empty() {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"detail": "Premiumize OAuth client ID not configured. Set PREMIUMIZE_OAUTH_CLIENT_ID environment variable."})),
        )
            .into_response();
    }

    let host_url = &state.config.host_url;
    let redirect_uri = format!("{}/streaming_provider/premiumize/oauth2_redirect", host_url);

    let mut url = format!(
        "https://www.premiumize.me/authorize?client_id={}&response_type=code&redirect_uri={}",
        client_id,
        urlencoding::encode(&redirect_uri),
    );

    if let Some(oauth_state) = params.state {
        url.push_str(&format!("&state={}", urlencoding::encode(&oauth_state)));
    }

    Redirect::temporary(&url).into_response()
}
