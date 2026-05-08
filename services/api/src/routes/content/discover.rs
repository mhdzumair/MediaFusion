/// Discover/trending feed endpoints (proxy to Python).
///
/// Routes (prefix /api/v1/discover):
///   GET /trending          → discover_trending
///   GET /list              → discover_list
///   GET /watch-providers   → discover_watch_providers
///   GET /provider-feed     → discover_provider_feed
///   GET /anime             → discover_anime
///   GET /search            → discover_search
///   GET /tvdb-filter       → discover_tvdb_filter
///   GET /mdblist           → discover_mdblist
///   GET /verify-tmdb-key   → verify_tmdb_key
use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Request, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, Mac};
use serde_json::json;
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth ─────────────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
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
    data["sub"].as_str()?.parse().ok()
}

// ─── Proxy helper ─────────────────────────────────────────────────────────────

async fn proxy_get(
    state: &Arc<AppState>,
    path: &str,
    query: &str,
    headers: &HeaderMap,
) -> Response {
    let base = match &state.config.python_proxy_url {
        Some(u) => u.clone(),
        None => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"detail": "Discover service not available in this deployment"})),
            )
                .into_response();
        }
    };

    let url = if query.is_empty() {
        format!("{base}{path}")
    } else {
        format!("{base}{path}?{query}")
    };

    let mut req = state.http.get(&url);
    for (key, val) in headers.iter() {
        let name = key.as_str().to_lowercase();
        if matches!(name.as_str(), "authorization" | "accept") {
            if let Ok(v) = val.to_str() {
                req = req.header(key.as_str(), v);
            }
        }
    }

    match req.send().await {
        Ok(resp) => {
            let status = StatusCode::from_u16(resp.status().as_u16())
                .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
            let bytes = resp.bytes().await.unwrap_or_default();
            (status, Body::from(bytes)).into_response()
        }
        Err(e) => {
            tracing::error!("discover proxy error: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "Failed to reach discover service"})),
            )
                .into_response()
        }
    }
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/discover/trending
pub async fn discover_trending(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    proxy_get(&state, "/api/v1/discover/trending", &q, &headers).await
}

/// GET /api/v1/discover/list
pub async fn discover_list(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    proxy_get(&state, "/api/v1/discover/list", &q, &headers).await
}

/// GET /api/v1/discover/watch-providers
pub async fn discover_watch_providers(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    proxy_get(&state, "/api/v1/discover/watch-providers", &q, &headers).await
}

/// GET /api/v1/discover/provider-feed
pub async fn discover_provider_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    proxy_get(&state, "/api/v1/discover/provider-feed", &q, &headers).await
}

/// GET /api/v1/discover/anime
pub async fn discover_anime(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    proxy_get(&state, "/api/v1/discover/anime", &q, &headers).await
}

/// GET /api/v1/discover/search
pub async fn discover_search(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    proxy_get(&state, "/api/v1/discover/search", &q, &headers).await
}

/// GET /api/v1/discover/tvdb-filter
pub async fn discover_tvdb_filter(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    proxy_get(&state, "/api/v1/discover/tvdb-filter", &q, &headers).await
}

/// GET /api/v1/discover/mdblist
pub async fn discover_mdblist(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    proxy_get(&state, "/api/v1/discover/mdblist", &q, &headers).await
}

/// GET /api/v1/discover/verify-tmdb-key
pub async fn verify_tmdb_key(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    proxy_get(&state, "/api/v1/discover/verify-tmdb-key", &q, &headers).await
}
