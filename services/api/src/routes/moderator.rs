/// Moderator metadata management endpoints.
///
/// All routes proxy to the Python service which hosts the business logic
/// (IMDB/TMDB search, apply external metadata, migrate IDs).
/// Requires moderator or admin JWT role.
///
/// Routes (prefix /api/v1/moderator/metadata):
///   GET  /                          → moderator_list_metadata
///   GET  /{media_id}                → moderator_get_metadata
///   POST /search-external           → moderator_search_external_metadata
///   POST /{media_id}/fetch-external → moderator_fetch_external_metadata
///   POST /{media_id}/apply-external → moderator_apply_external_metadata
///   POST /{media_id}/migrate-id     → moderator_migrate_metadata_id
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helper ──────────────────────────────────────────────────────────────

fn validate_moderator_token(headers: &HeaderMap, secret_key: &str) -> Option<(i64, String)> {
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
    let data: Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    let user_id: i64 = data["sub"].as_str()?.parse().ok()?;
    let role = data["role"].as_str().unwrap_or("user").to_string();
    // Allow both moderator and admin roles
    if role != "moderator" && role != "admin" {
        return None;
    }
    Some((user_id, role))
}

// ─── Query params ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ListMetadataQuery {
    pub page: Option<i64>,
    pub per_page: Option<i64>,
    pub media_type: Option<String>,
    pub search: Option<String>,
    pub has_streams: Option<bool>,
}

// ─── Proxy helper ─────────────────────────────────────────────────────────────

async fn proxy_to_python(
    state: &AppState,
    method: reqwest::Method,
    path: &str,
    headers: &HeaderMap,
    body: Option<Value>,
) -> axum::response::Response {
    let py_url = match &state.config.python_proxy_url {
        Some(u) => u.clone(),
        None => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"detail": "Background service unavailable"})),
            )
                .into_response();
        }
    };

    let url = format!("{py_url}{path}");
    let mut req = state.http.request(method, &url);

    if let Some(auth) = headers.get("authorization") {
        req = req.header("authorization", auth);
    }

    if let Some(b) = body {
        req = req.json(&b);
    }

    match req.send().await {
        Ok(r) => {
            let status = StatusCode::from_u16(r.status().as_u16())
                .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
            let body: Value = r.json().await.unwrap_or(json!({}));
            (status, Json(body)).into_response()
        }
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"detail": e.to_string()})),
        )
            .into_response(),
    }
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/moderator/metadata
pub async fn moderator_list_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListMetadataQuery>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    let mut path = "/api/v1/moderator/metadata?".to_string();
    if let Some(p) = params.page {
        path.push_str(&format!("page={p}&"));
    }
    if let Some(pp) = params.per_page {
        path.push_str(&format!("per_page={pp}&"));
    }
    if let Some(ref mt) = params.media_type {
        path.push_str(&format!("media_type={mt}&"));
    }
    if let Some(ref s) = params.search {
        path.push_str(&format!("search={}&", urlencoding::encode(s)));
    }
    if let Some(hs) = params.has_streams {
        path.push_str(&format!("has_streams={hs}&"));
    }

    proxy_to_python(
        &state,
        reqwest::Method::GET,
        path.trim_end_matches('&'),
        &headers,
        None,
    )
    .await
}

/// GET /api/v1/moderator/metadata/{media_id}
pub async fn moderator_get_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    let path = format!("/api/v1/moderator/metadata/{media_id}");
    proxy_to_python(&state, reqwest::Method::GET, &path, &headers, None).await
}

/// POST /api/v1/moderator/metadata/search-external
pub async fn moderator_search_external_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/moderator/metadata/search-external",
        &headers,
        Some(body),
    )
    .await
}

/// POST /api/v1/moderator/metadata/{media_id}/fetch-external
pub async fn moderator_fetch_external_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    let path = format!("/api/v1/moderator/metadata/{media_id}/fetch-external");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, Some(body)).await
}

/// POST /api/v1/moderator/metadata/{media_id}/apply-external
pub async fn moderator_apply_external_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    let path = format!("/api/v1/moderator/metadata/{media_id}/apply-external");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, Some(body)).await
}

/// POST /api/v1/moderator/metadata/{media_id}/migrate-id
pub async fn moderator_migrate_metadata_id(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    let path = format!("/api/v1/moderator/metadata/{media_id}/migrate-id");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, Some(body)).await
}
