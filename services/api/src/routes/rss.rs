/// RSS feed management endpoints.
///
/// Admin RSS routes (prefix /api/v1/admin/rss):
///   GET    /feeds                          → list_rss_feeds
///   GET    /feeds/{feed_id}                → get_rss_feed
///   POST   /feeds                          → create_rss_feed
///   PUT    /feeds/{feed_id}                → update_rss_feed
///   DELETE /feeds/{feed_id}                → delete_rss_feed
///   POST   /feeds/bulk-import              → bulk_import_rss_feeds
///   POST   /feeds/run                      → run_rss_feed_scraper
///   POST   /feeds/test-feed                → test_rss_feed
///   POST   /feeds/activate-deactivate-feeds→ activate_deactivate_feeds
///
/// User RSS routes (prefix /api/v1/user-rss):
///   GET    /feeds                          → user_list_rss_feeds
///   GET    /feeds/{feed_id}                → user_get_rss_feed
///   POST   /feeds                          → user_create_rss_feed
///   PUT    /feeds/{feed_id}                → user_update_rss_feed
///   DELETE /feeds/{feed_id}                → user_delete_rss_feed
///   POST   /feeds/{feed_id}/test           → user_test_rss_feed
///   POST   /feeds/test-url                 → user_test_rss_feed_url
///   POST   /feeds/{feed_id}/scrape         → user_scrape_single_feed
///   POST   /feeds/run-all                  → user_run_all_scrapers
///   POST   /feeds/bulk-status              → user_bulk_update_feed_status
///   GET    /scheduler-status               → user_get_scheduler_status
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helpers ──────────────────────────────────────────────────────────────

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
    let data: Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

fn validate_token_and_role(headers: &HeaderMap, secret_key: &str) -> Option<(i32, String)> {
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
    let user_id: i32 = data["sub"].as_str()?.parse().ok()?;
    let role = data["role"].as_str().unwrap_or("user").to_string();
    Some((user_id, role))
}

fn validate_admin_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
    let (user_id, role) = validate_token_and_role(headers, secret_key)?;
    if role == "admin" {
        Some(user_id)
    } else {
        None
    }
}

// ─── Request / Response types ─────────────────────────────────────────────────

#[derive(Deserialize, Serialize)]
pub struct RSSFeedCreate {
    pub name: String,
    pub url: String,
    pub active: Option<bool>,
    pub source: Option<String>,
    pub torrent_type: Option<String>,
    pub auto_detect_catalog: Option<bool>,
    pub parsing_patterns: Option<Value>,
    pub filters: Option<Value>,
    pub catalog_patterns: Option<Value>,
}

#[derive(Deserialize, Serialize)]
pub struct RSSFeedUpdate {
    pub name: Option<String>,
    pub url: Option<String>,
    pub active: Option<bool>,
    pub source: Option<String>,
    pub torrent_type: Option<String>,
    pub auto_detect_catalog: Option<bool>,
    pub parsing_patterns: Option<Value>,
    pub filters: Option<Value>,
    pub catalog_patterns: Option<Value>,
}

#[derive(Deserialize, Serialize)]
pub struct BulkImportRequest {
    pub api_password: Option<String>,
    pub feeds: Vec<Value>,
}

#[derive(Deserialize, Serialize)]
pub struct TestFeedRequest {
    pub url: String,
    pub patterns: Option<Value>,
}

#[derive(Deserialize, Serialize)]
pub struct ActivateDeactivateRequest {
    pub feed_ids: Vec<i32>,
    pub activate: bool,
}

#[derive(Deserialize)]
pub struct UserRSSFeedCreate {
    pub name: String,
    pub url: String,
    pub is_active: Option<bool>,
    pub source: Option<String>,
    pub torrent_type: Option<String>,
    pub auto_detect_catalog: Option<bool>,
    pub parsing_patterns: Option<Value>,
    pub filters: Option<Value>,
    pub catalog_patterns: Option<Value>,
}

#[derive(Deserialize)]
pub struct UserRSSFeedUpdate {
    pub name: Option<String>,
    pub url: Option<String>,
    pub is_active: Option<bool>,
    pub source: Option<String>,
    pub torrent_type: Option<String>,
    pub auto_detect_catalog: Option<bool>,
    pub parsing_patterns: Option<Value>,
    pub filters: Option<Value>,
    pub catalog_patterns: Option<Value>,
}

#[derive(Deserialize, Serialize)]
pub struct BulkStatusRequest {
    pub feed_ids: Vec<String>,
    pub is_active: bool,
}

#[derive(Deserialize, Serialize)]
pub struct UserTestFeedRequest {
    pub url: String,
    pub patterns: Option<Value>,
}

// ─── Admin RSS endpoints (proxy to Python) ───────────────────────────────────

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

    // Forward auth header
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

/// GET /api/v1/admin/rss/feeds
pub async fn list_rss_feeds(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/rss/feeds",
        &headers,
        None,
    )
    .await
}

/// GET /api/v1/admin/rss/feeds/{feed_id}
pub async fn get_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<i32>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    let path = format!("/api/v1/admin/rss/feeds/{feed_id}");
    proxy_to_python(&state, reqwest::Method::GET, &path, &headers, None).await
}

/// POST /api/v1/admin/rss/feeds
pub async fn create_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<RSSFeedCreate>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/rss/feeds",
        &headers,
        Some(serde_json::to_value(body).unwrap_or(json!({}))),
    )
    .await
}

/// PUT /api/v1/admin/rss/feeds/{feed_id}
pub async fn update_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<i32>,
    Json(body): Json<RSSFeedUpdate>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    let path = format!("/api/v1/admin/rss/feeds/{feed_id}");
    proxy_to_python(
        &state,
        reqwest::Method::PUT,
        &path,
        &headers,
        Some(serde_json::to_value(body).unwrap_or(json!({}))),
    )
    .await
}

/// DELETE /api/v1/admin/rss/feeds/{feed_id}
pub async fn delete_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<i32>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    let path = format!("/api/v1/admin/rss/feeds/{feed_id}");
    proxy_to_python(&state, reqwest::Method::DELETE, &path, &headers, None).await
}

/// POST /api/v1/admin/rss/feeds/bulk-import
pub async fn bulk_import_rss_feeds(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkImportRequest>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/rss/feeds/bulk-import",
        &headers,
        Some(serde_json::to_value(body).unwrap_or(json!({}))),
    )
    .await
}

/// POST /api/v1/admin/rss/feeds/run
pub async fn run_rss_feed_scraper(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/rss/feeds/run",
        &headers,
        None,
    )
    .await
}

/// POST /api/v1/admin/rss/feeds/test-feed
pub async fn test_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<TestFeedRequest>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/rss/feeds/test-feed",
        &headers,
        Some(serde_json::to_value(body).unwrap_or(json!({}))),
    )
    .await
}

/// POST /api/v1/admin/rss/feeds/activate-deactivate-feeds
pub async fn activate_deactivate_feeds(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<ActivateDeactivateRequest>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/rss/feeds/activate-deactivate-feeds",
        &headers,
        Some(serde_json::to_value(body).unwrap_or(json!({}))),
    )
    .await
}

// ─── User RSS endpoints ───────────────────────────────────────────────────────

/// GET /api/v1/user-rss/feeds
pub async fn user_list_rss_feeds(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    // Fetch user's RSS feeds from DB
    let rows = sqlx::query_as::<_, (i32, String, String, bool, Option<String>, Option<String>)>(
        r#"SELECT id, name, url, is_active, source, torrent_type
           FROM rss_feed WHERE user_id = $1
           ORDER BY created_at DESC"#,
    )
    .bind(user_id)
    .fetch_all(&state.pool_ro)
    .await;

    match rows {
        Ok(feeds) => {
            let items: Vec<Value> = feeds
                .into_iter()
                .map(|(id, name, url, is_active, source, torrent_type)| {
                    json!({
                        "id": id,
                        "name": name,
                        "url": url,
                        "is_active": is_active,
                        "source": source,
                        "torrent_type": torrent_type.unwrap_or_else(|| "public".to_string()),
                    })
                })
                .collect();
            Json(items).into_response()
        }
        Err(e) => {
            tracing::error!("user_list_rss_feeds: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// GET /api/v1/user-rss/feeds/{feed_id}
pub async fn user_get_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<String>,
) -> impl IntoResponse {
    let (user_id, role) = match validate_token_and_role(&headers, &state.config.secret_key_raw) {
        Some(r) => r,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let user_id_filter: Option<i32> = if role == "admin" { None } else { Some(user_id) };

    let row = if let Some(uid) = user_id_filter {
        sqlx::query_as::<_, (i32, String, String, bool, Option<String>, Option<String>)>(
            "SELECT id, name, url, is_active, source, torrent_type FROM rss_feed WHERE id::text = $1 AND user_id = $2",
        )
        .bind(&feed_id)
        .bind(uid)
        .fetch_optional(&state.pool_ro)
        .await
    } else {
        sqlx::query_as::<_, (i32, String, String, bool, Option<String>, Option<String>)>(
            "SELECT id, name, url, is_active, source, torrent_type FROM rss_feed WHERE id::text = $1",
        )
        .bind(&feed_id)
        .fetch_optional(&state.pool_ro)
        .await
    };

    match row {
        Ok(Some((id, name, url, is_active, source, torrent_type))) => Json(json!({
            "id": id,
            "name": name,
            "url": url,
            "is_active": is_active,
            "source": source,
            "torrent_type": torrent_type.unwrap_or_else(|| "public".to_string()),
        }))
        .into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("RSS feed with ID {feed_id} not found")})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("user_get_rss_feed: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// POST /api/v1/user-rss/feeds
pub async fn user_create_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<UserRSSFeedCreate>,
) -> impl IntoResponse {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    // Check duplicate URL for this user
    let existing: Option<i64> =
        sqlx::query_scalar("SELECT id FROM rss_feed WHERE url = $1 AND user_id = $2")
            .bind(&body.url)
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if existing.is_some() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": format!("You already have an RSS feed with URL: {}", body.url)})),
        )
            .into_response();
    }

    let is_active = body.is_active.unwrap_or(true);
    let torrent_type = body.torrent_type.as_deref().unwrap_or("public");

    let id: i32 = match sqlx::query_scalar(
        r#"INSERT INTO rss_feed (user_id, name, url, is_active, source, torrent_type, auto_detect_catalog, parsing_patterns, filters, catalog_patterns, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
           RETURNING id"#,
    )
    .bind(user_id)
    .bind(&body.name)
    .bind(&body.url)
    .bind(is_active)
    .bind(&body.source)
    .bind(torrent_type)
    .bind(body.auto_detect_catalog.unwrap_or(false))
    .bind(body.parsing_patterns.as_ref().map(|v| v.to_string()))
    .bind(body.filters.as_ref().map(|v| v.to_string()))
    .bind(body.catalog_patterns.as_ref().map(|v| v.to_string()))
    .fetch_one(&state.pool)
    .await
    {
        Ok(id) => id,
        Err(e) => {
            tracing::error!("user_create_rss_feed: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    (
        StatusCode::CREATED,
        Json(json!({
            "id": id,
            "name": body.name,
            "url": body.url,
            "is_active": is_active,
            "source": body.source,
            "torrent_type": torrent_type,
        })),
    )
        .into_response()
}

/// PUT /api/v1/user-rss/feeds/{feed_id}
pub async fn user_update_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<String>,
    Json(body): Json<UserRSSFeedUpdate>,
) -> impl IntoResponse {
    let (user_id, role) = match validate_token_and_role(&headers, &state.config.secret_key_raw) {
        Some(r) => r,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let user_id_filter: Option<i32> = if role == "admin" { None } else { Some(user_id) };

    // Build update query dynamically
    let check_result = if let Some(uid) = user_id_filter {
        sqlx::query_scalar::<_, i64>("SELECT id FROM rss_feed WHERE id::text = $1 AND user_id = $2")
            .bind(&feed_id)
            .bind(uid)
            .fetch_optional(&state.pool)
            .await
    } else {
        sqlx::query_scalar::<_, i64>("SELECT id FROM rss_feed WHERE id::text = $1")
            .bind(&feed_id)
            .fetch_optional(&state.pool)
            .await
    };

    let db_id = match check_result {
        Ok(Some(id)) => id,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": format!("RSS feed with ID {feed_id} not found")})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("user_update_rss_feed check: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    // Apply updates
    if let Some(name) = &body.name {
        let _ = sqlx::query("UPDATE rss_feed SET name = $1, updated_at = NOW() WHERE id = $2")
            .bind(name)
            .bind(db_id)
            .execute(&state.pool)
            .await;
    }
    if let Some(url) = &body.url {
        let _ = sqlx::query("UPDATE rss_feed SET url = $1, updated_at = NOW() WHERE id = $2")
            .bind(url)
            .bind(db_id)
            .execute(&state.pool)
            .await;
    }
    if let Some(is_active) = body.is_active {
        let _ = sqlx::query("UPDATE rss_feed SET is_active = $1, updated_at = NOW() WHERE id = $2")
            .bind(is_active)
            .bind(db_id)
            .execute(&state.pool)
            .await;
    }

    Json(json!({"id": db_id, "detail": "Updated successfully"})).into_response()
}

/// DELETE /api/v1/user-rss/feeds/{feed_id}
pub async fn user_delete_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<String>,
) -> impl IntoResponse {
    let (user_id, role) = match validate_token_and_role(&headers, &state.config.secret_key_raw) {
        Some(r) => r,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let result = if role == "admin" {
        sqlx::query("DELETE FROM rss_feed WHERE id::text = $1")
            .bind(&feed_id)
            .execute(&state.pool)
            .await
    } else {
        sqlx::query("DELETE FROM rss_feed WHERE id::text = $1 AND user_id = $2")
            .bind(&feed_id)
            .bind(user_id)
            .execute(&state.pool)
            .await
    };

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("RSS feed with ID {feed_id} not found")})),
        )
            .into_response(),
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("user_delete_rss_feed: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// POST /api/v1/user-rss/feeds/{feed_id}/test
pub async fn user_test_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<String>,
) -> impl IntoResponse {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };
    let _ = user_id;

    // Proxy to Python for actual RSS fetching
    let path = format!("/api/v1/user-rss/feeds/{feed_id}/test");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, None).await
}

/// POST /api/v1/user-rss/feeds/test-url
pub async fn user_test_rss_feed_url(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<UserTestFeedRequest>,
) -> impl IntoResponse {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/user-rss/feeds/test-url",
        &headers,
        Some(serde_json::to_value(body).unwrap_or(json!({}))),
    )
    .await
}

/// POST /api/v1/user-rss/feeds/{feed_id}/scrape
pub async fn user_scrape_single_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<String>,
) -> impl IntoResponse {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let path = format!("/api/v1/user-rss/feeds/{feed_id}/scrape");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, None).await
}

/// POST /api/v1/user-rss/feeds/run-all
pub async fn user_run_all_scrapers(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    match validate_token_and_role(&headers, &state.config.secret_key_raw) {
        Some((_, role)) if role == "admin" => {}
        Some(_) => {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"detail": "Only admins can run the global scraper"})),
            )
                .into_response();
        }
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/user-rss/feeds/run-all",
        &headers,
        None,
    )
    .await
}

/// POST /api/v1/user-rss/feeds/bulk-status
pub async fn user_bulk_update_feed_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkStatusRequest>,
) -> impl IntoResponse {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/user-rss/feeds/bulk-status",
        &headers,
        Some(serde_json::to_value(body).unwrap_or(json!({}))),
    )
    .await
}

/// GET /api/v1/user-rss/scheduler-status
pub async fn user_get_scheduler_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/user-rss/scheduler-status",
        &headers,
        None,
    )
    .await
}
