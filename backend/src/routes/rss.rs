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

#[allow(unused_imports)]
use axum::extract::Query;
use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
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

// ─── Helper: full RSS feed JSON ───────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
fn rss_feed_json(
    id: i32,
    uuid: &str,
    name: &str,
    url: &str,
    is_active: bool,
    is_public: bool,
    source: Option<&str>,
    torrent_type: &str,
    auto_detect_catalog: bool,
    parsing_patterns: Option<Value>,
    filters: Option<Value>,
    metrics: Option<Value>,
    last_scraped_at: Option<chrono::DateTime<chrono::Utc>>,
    created_at: chrono::DateTime<chrono::Utc>,
    updated_at: Option<chrono::DateTime<chrono::Utc>>,
) -> Value {
    json!({
        "id": id,
        "uuid": uuid,
        "name": name,
        "url": url,
        "is_active": is_active,
        "is_public": is_public,
        "source": source,
        "torrent_type": torrent_type,
        "auto_detect_catalog": auto_detect_catalog,
        "parsing_patterns": parsing_patterns,
        "filters": filters,
        "metrics": metrics,
        "last_scraped_at": last_scraped_at,
        "created_at": created_at,
        "updated_at": updated_at,
    })
}

// ─── Admin RSS endpoints ──────────────────────────────────────────────────────

/// GET /api/v1/admin/rss/feeds
pub async fn list_rss_feeds(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    type FeedRow = (
        i32,
        String,
        String,
        String,
        bool,
        bool,
        Option<String>,
        String,
        bool,
        Option<Value>,
        Option<Value>,
        Option<Value>,
        Option<chrono::DateTime<chrono::Utc>>,
        chrono::DateTime<chrono::Utc>,
        Option<chrono::DateTime<chrono::Utc>>,
    );

    let rows: Vec<FeedRow> = sqlx::query_as(
        "SELECT id, uuid, name, url, is_active, is_public, source, torrent_type, auto_detect_catalog, \
                parsing_patterns, filters, metrics, last_scraped_at, created_at, updated_at \
         FROM rss_feed ORDER BY created_at DESC",
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let items: Vec<Value> = rows
        .iter()
        .map(|r| {
            rss_feed_json(
                r.0,
                &r.1,
                &r.2,
                &r.3,
                r.4,
                r.5,
                r.6.as_deref(),
                &r.7,
                r.8,
                r.9.clone(),
                r.10.clone(),
                r.11.clone(),
                r.12,
                r.13,
                r.14,
            )
        })
        .collect();

    Json(items).into_response()
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

    type FeedRow = (
        i32,
        String,
        String,
        String,
        bool,
        bool,
        Option<String>,
        String,
        bool,
        Option<Value>,
        Option<Value>,
        Option<Value>,
        Option<chrono::DateTime<chrono::Utc>>,
        chrono::DateTime<chrono::Utc>,
        Option<chrono::DateTime<chrono::Utc>>,
    );

    let row: Option<FeedRow> = sqlx::query_as(
        "SELECT id, uuid, name, url, is_active, is_public, source, torrent_type, auto_detect_catalog, \
                parsing_patterns, filters, metrics, last_scraped_at, created_at, updated_at \
         FROM rss_feed WHERE id = $1",
    )
    .bind(feed_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    match row {
        Some(r) => Json(rss_feed_json(
            r.0,
            &r.1,
            &r.2,
            &r.3,
            r.4,
            r.5,
            r.6.as_deref(),
            &r.7,
            r.8,
            r.9,
            r.10,
            r.11,
            r.12,
            r.13,
            r.14,
        ))
        .into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("RSS feed with ID {feed_id} not found")})),
        )
            .into_response(),
    }
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

    // Get admin user id for user_id FK — use first admin user
    let admin_id: Option<i32> = sqlx::query_scalar("SELECT id FROM users WHERE role = $1 LIMIT 1")
        .bind(crate::db::UserRole::Admin)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    let admin_id = match admin_id {
        Some(id) => id,
        None => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "No admin user found to own the feed"})),
            )
                .into_response();
        }
    };

    // Check duplicate URL (globally for admin feeds)
    let existing: Option<i64> =
        sqlx::query_scalar("SELECT id FROM rss_feed WHERE url = $1 LIMIT 1")
            .bind(&body.url)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if existing.is_some() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": format!("RSS feed with URL {} already exists", body.url)})),
        )
            .into_response();
    }

    let is_active = body.active.unwrap_or(true);
    let torrent_type = body.torrent_type.as_deref().unwrap_or("public");
    let parsing_patterns_str = body
        .parsing_patterns
        .as_ref()
        .and_then(|v| serde_json::to_string(v).ok());
    let filters_str = body
        .filters
        .as_ref()
        .and_then(|v| serde_json::to_string(v).ok());

    type FeedRow = (
        i32,
        String,
        String,
        String,
        bool,
        bool,
        Option<String>,
        String,
        bool,
        Option<Value>,
        Option<Value>,
        Option<Value>,
        Option<chrono::DateTime<chrono::Utc>>,
        chrono::DateTime<chrono::Utc>,
        Option<chrono::DateTime<chrono::Utc>>,
    );

    let row: Option<FeedRow> = sqlx::query_as(
        "INSERT INTO rss_feed (uuid, user_id, name, url, is_active, is_public, source, torrent_type, \
                               auto_detect_catalog, parsing_patterns, filters, created_at, updated_at) \
         VALUES (gen_random_uuid()::text, $1, $2, $3, $4, false, $5, $6, $7, $8::json, $9::json, NOW(), NOW()) \
         RETURNING id, uuid, name, url, is_active, is_public, source, torrent_type, auto_detect_catalog, \
                   parsing_patterns, filters, metrics, last_scraped_at, created_at, updated_at",
    )
    .bind(admin_id)
    .bind(&body.name)
    .bind(&body.url)
    .bind(is_active)
    .bind(&body.source)
    .bind(torrent_type)
    .bind(body.auto_detect_catalog.unwrap_or(false))
    .bind(parsing_patterns_str)
    .bind(filters_str)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    match row {
        Some(r) => (
            StatusCode::CREATED,
            Json(rss_feed_json(
                r.0,
                &r.1,
                &r.2,
                &r.3,
                r.4,
                r.5,
                r.6.as_deref(),
                &r.7,
                r.8,
                r.9,
                r.10,
                r.11,
                r.12,
                r.13,
                r.14,
            )),
        )
            .into_response(),
        None => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
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

    // Verify exists
    let exists: Option<i32> = sqlx::query_scalar("SELECT id FROM rss_feed WHERE id = $1")
        .bind(feed_id)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None);

    if exists.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("RSS feed with ID {feed_id} not found")})),
        )
            .into_response();
    }

    if let Some(name) = &body.name {
        let _ = sqlx::query("UPDATE rss_feed SET name = $1, updated_at = NOW() WHERE id = $2")
            .bind(name)
            .bind(feed_id)
            .execute(&state.pool)
            .await;
    }
    if let Some(url) = &body.url {
        let _ = sqlx::query("UPDATE rss_feed SET url = $1, updated_at = NOW() WHERE id = $2")
            .bind(url)
            .bind(feed_id)
            .execute(&state.pool)
            .await;
    }
    if let Some(active) = body.active {
        let _ = sqlx::query("UPDATE rss_feed SET is_active = $1, updated_at = NOW() WHERE id = $2")
            .bind(active)
            .bind(feed_id)
            .execute(&state.pool)
            .await;
    }
    if let Some(source) = &body.source {
        let _ = sqlx::query("UPDATE rss_feed SET source = $1, updated_at = NOW() WHERE id = $2")
            .bind(source)
            .bind(feed_id)
            .execute(&state.pool)
            .await;
    }
    if let Some(tt) = &body.torrent_type {
        let _ =
            sqlx::query("UPDATE rss_feed SET torrent_type = $1, updated_at = NOW() WHERE id = $2")
                .bind(tt)
                .bind(feed_id)
                .execute(&state.pool)
                .await;
    }
    if let Some(adc) = body.auto_detect_catalog {
        let _ = sqlx::query(
            "UPDATE rss_feed SET auto_detect_catalog = $1, updated_at = NOW() WHERE id = $2",
        )
        .bind(adc)
        .bind(feed_id)
        .execute(&state.pool)
        .await;
    }
    if let Some(pp) = &body.parsing_patterns {
        if let Ok(s) = serde_json::to_string(pp) {
            let _ = sqlx::query(
                "UPDATE rss_feed SET parsing_patterns = $1::json, updated_at = NOW() WHERE id = $2",
            )
            .bind(s)
            .bind(feed_id)
            .execute(&state.pool)
            .await;
        }
    }
    if let Some(f) = &body.filters {
        if let Ok(s) = serde_json::to_string(f) {
            let _ = sqlx::query(
                "UPDATE rss_feed SET filters = $1::json, updated_at = NOW() WHERE id = $2",
            )
            .bind(s)
            .bind(feed_id)
            .execute(&state.pool)
            .await;
        }
    }

    // Return updated feed
    type FeedRow = (
        i32,
        String,
        String,
        String,
        bool,
        bool,
        Option<String>,
        String,
        bool,
        Option<Value>,
        Option<Value>,
        Option<Value>,
        Option<chrono::DateTime<chrono::Utc>>,
        chrono::DateTime<chrono::Utc>,
        Option<chrono::DateTime<chrono::Utc>>,
    );

    let row: Option<FeedRow> = sqlx::query_as(
        "SELECT id, uuid, name, url, is_active, is_public, source, torrent_type, auto_detect_catalog, \
                parsing_patterns, filters, metrics, last_scraped_at, created_at, updated_at \
         FROM rss_feed WHERE id = $1",
    )
    .bind(feed_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    match row {
        Some(r) => Json(rss_feed_json(
            r.0,
            &r.1,
            &r.2,
            &r.3,
            r.4,
            r.5,
            r.6.as_deref(),
            &r.7,
            r.8,
            r.9,
            r.10,
            r.11,
            r.12,
            r.13,
            r.14,
        ))
        .into_response(),
        None => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
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

    let result = sqlx::query("DELETE FROM rss_feed WHERE id = $1")
        .bind(feed_id)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("RSS feed with ID {feed_id} not found")})),
        )
            .into_response(),
        Ok(_) => Json(json!({"detail": format!("RSS feed {feed_id} deleted successfully")}))
            .into_response(),
        Err(e) => {
            tracing::error!("delete_rss_feed: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
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

    // Verify api_password
    if state.config.api_password.as_deref() != body.api_password.as_deref() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Invalid API password"})),
        )
            .into_response();
    }

    let admin_id: Option<i32> = sqlx::query_scalar("SELECT id FROM users WHERE role = $1 LIMIT 1")
        .bind(crate::db::UserRole::Admin)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    let admin_id = match admin_id {
        Some(id) => id,
        None => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "No admin user found"})),
            )
                .into_response();
        }
    };

    // Get existing URLs
    let existing_urls: Vec<String> = sqlx::query_scalar("SELECT url FROM rss_feed")
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

    let mut imported: Vec<String> = Vec::new();
    let mut skipped: Vec<String> = Vec::new();

    for feed in &body.feeds {
        let url = match feed.get("url").and_then(|v| v.as_str()) {
            Some(u) => u.to_string(),
            None => continue,
        };
        if existing_urls.contains(&url) {
            skipped.push(url);
            continue;
        }
        let name = feed
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("Unnamed");
        let active = feed.get("active").and_then(|v| v.as_bool()).unwrap_or(true);
        let parsing_patterns = feed
            .get("parsing_patterns")
            .and_then(|v| serde_json::to_string(v).ok());

        let id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO rss_feed (uuid, user_id, name, url, is_active, is_public, torrent_type, auto_detect_catalog, parsing_patterns, created_at, updated_at) \
             VALUES (gen_random_uuid()::text, $1, $2, $3, $4, false, 'public', false, $5::json, NOW(), NOW()) \
             RETURNING id",
        )
        .bind(admin_id)
        .bind(name)
        .bind(&url)
        .bind(active)
        .bind(parsing_patterns)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None);

        if let Some(id) = id {
            imported.push(id.to_string());
        }
    }

    Json(json!({
        "detail": format!("Imported {} RSS feeds, skipped {} duplicates", imported.len(), skipped.len()),
        "imported": imported,
        "skipped": skipped,
    }))
    .into_response()
}

/// POST /api/v1/admin/rss/{id}/scrape  (admin: run single feed by id)
pub async fn run_rss_feed_scraper(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<i32>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    // Load the specific feed
    type FeedRow = (
        i32,
        String,
        String,
        Option<String>,
        Option<Value>,
        Option<Value>,
        bool,
        String,
    );
    let row: Option<FeedRow> = sqlx::query_as(
        "SELECT id, url, name, source, parsing_patterns, filters, auto_detect_catalog, torrent_type::text FROM rss_feed WHERE id = $1",
    )
    .bind(feed_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let (db_id, url, name, source, patterns, filters, auto_detect, feed_torrent_type) = match row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": format!("RSS feed with ID {feed_id} not found")})),
            )
                .into_response();
        }
    };

    let pool = state.pool.clone();
    let http = state.http.clone();
    let tmdb_key = state.config.tmdb_api_key.clone();
    let cinemeta_fallback = state.config.imdb_cinemeta_fallback_enabled;
    let feed_type = crate::scrapers::torrent_metadata::parse_torrent_type_str(&feed_torrent_type);
    tokio::spawn(async move {
        crate::scrapers::rss::scrape_feed(
            &pool,
            &http,
            db_id,
            &url,
            &name,
            source.as_deref(),
            patterns.as_ref(),
            filters.as_ref(),
            auto_detect,
            feed_type,
            tmdb_key.as_deref(),
            cinemeta_fallback,
        )
        .await;
    });

    Json(json!({
        "detail": "RSS feed scraper started",
        "feed_count": 1,
    }))
    .into_response()
}

/// POST /api/v1/admin/rss/{id}/test
pub async fn test_rss_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(feed_id): Path<i32>,
) -> impl IntoResponse {
    if validate_admin_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    // Load the feed from DB
    type FeedRow = (String, String, Option<Value>); // url, name, parsing_patterns
    let row: Option<FeedRow> =
        sqlx::query_as("SELECT url, name, parsing_patterns FROM rss_feed WHERE id = $1")
            .bind(feed_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    let (url, name, patterns) = match row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": format!("RSS feed with ID {feed_id} not found")})),
            )
                .into_response();
        }
    };

    // Fetch XML
    let xml = match state
        .http
        .get(&url)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.text().await.unwrap_or_default(),
        _ => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"status": "error", "message": "Could not fetch feed URL"})),
            )
                .into_response();
        }
    };

    let items = crate::scrapers::rss::parse_rss_xml(&xml);
    let count = items.len();
    if count == 0 {
        return Json(json!({"status": "error", "message": "No items found in feed"}))
            .into_response();
    }

    let empty = Value::Object(Default::default());
    let pat = patterns.as_ref().unwrap_or(&empty);
    let first = &items[0];

    let sample_title = first.title.as_deref().unwrap_or("");
    let sample_hash = crate::scrapers::rss::extract_info_hash_pub(first, pat);
    let sample_size = crate::scrapers::rss::extract_size_pub(first, pat);
    let parsed = crate::parser::parse_title(sample_title);

    let sample = json!({
        "title": sample_title,
        "info_hash": sample_hash,
        "size_bytes": sample_size,
        "link": first.link,
        "description": first.description.as_deref().map(|d| &d[..d.len().min(200)]),
        "parsed_title": parsed.title,
        "parsed_year": parsed.year,
        "seasons": parsed.seasons,
        "episodes": parsed.episodes,
    });

    Json(json!({
        "status": "success",
        "message": format!("Successfully fetched feed '{}' with {count} items", name),
        "items_count": count,
        "feed_name": name,
        "sample_item": sample,
    }))
    .into_response()
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

    let result =
        sqlx::query("UPDATE rss_feed SET is_active = $1, updated_at = NOW() WHERE id = ANY($2)")
            .bind(body.activate)
            .bind(&body.feed_ids)
            .execute(&state.pool)
            .await;

    match result {
        Ok(r) => {
            let action = if body.activate {
                "activated"
            } else {
                "deactivated"
            };
            Json(json!({"detail": format!("Successfully {} {} RSS feeds", action, r.rows_affected())}))
                .into_response()
        }
        Err(e) => {
            tracing::error!("activate_deactivate_feeds: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

// ─── User RSS endpoints ───────────────────────────────────────────────────────

// ─── Helper: full user RSS feed JSON (includes user sub-object) ──────────────

#[allow(clippy::too_many_arguments)]
fn user_feed_json(
    id: i32,
    user_id: i32,
    name: &str,
    url: &str,
    is_active: bool,
    source: Option<&str>,
    torrent_type: &str,
    auto_detect_catalog: bool,
    parsing_patterns: Option<Value>,
    filters: Option<Value>,
    metrics: Option<Value>,
    catalog_patterns: Option<Value>,
    last_scraped_at: Option<chrono::DateTime<chrono::Utc>>,
    created_at: chrono::DateTime<chrono::Utc>,
    updated_at: Option<chrono::DateTime<chrono::Utc>>,
    user_email: &str,
    user_username: Option<&str>,
) -> Value {
    json!({
        "id": id,
        "user_id": user_id,
        "name": name,
        "url": url,
        "is_active": is_active,
        "source": source,
        "torrent_type": torrent_type,
        "auto_detect_catalog": auto_detect_catalog,
        "parsing_patterns": parsing_patterns,
        "filters": filters,
        "metrics": metrics,
        "catalog_patterns": catalog_patterns,
        "last_scraped_at": last_scraped_at,
        "created_at": created_at,
        "updated_at": updated_at,
        "user": {
            "id": user_id,
            "email": user_email,
            "username": user_username,
        }
    })
}

#[derive(sqlx::FromRow)]
struct UserFeedRow {
    id: i32,
    user_id: i32,
    name: String,
    url: String,
    is_active: bool,
    source: Option<String>,
    torrent_type: String,
    auto_detect_catalog: bool,
    parsing_patterns: Option<Value>,
    filters: Option<Value>,
    metrics: Option<Value>,
    last_scraped_at: Option<chrono::DateTime<chrono::Utc>>,
    created_at: chrono::DateTime<chrono::Utc>,
    updated_at: Option<chrono::DateTime<chrono::Utc>>,
    user_email: String,
    user_username: Option<String>,
}

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

    let rows = sqlx::query_as::<_, UserFeedRow>(
        r#"SELECT f.id, f.user_id, f.name, f.url, f.is_active, f.source, f.torrent_type,
                  f.auto_detect_catalog, f.parsing_patterns, f.filters, f.metrics,
                  f.last_scraped_at, f.created_at, f.updated_at,
                  u.email AS user_email, u.username AS user_username
           FROM rss_feed f
           JOIN users u ON u.id = f.user_id
           WHERE f.user_id = $1
           ORDER BY f.created_at DESC"#,
    )
    .bind(user_id)
    .fetch_all(&state.pool_ro)
    .await;

    match rows {
        Ok(feeds) => {
            let items: Vec<Value> = feeds
                .into_iter()
                .map(|r| {
                    user_feed_json(
                        r.id,
                        r.user_id,
                        &r.name,
                        &r.url,
                        r.is_active,
                        r.source.as_deref(),
                        &r.torrent_type,
                        r.auto_detect_catalog,
                        r.parsing_patterns,
                        r.filters,
                        r.metrics,
                        None,
                        r.last_scraped_at,
                        r.created_at,
                        r.updated_at,
                        &r.user_email,
                        r.user_username.as_deref(),
                    )
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
        r#"INSERT INTO rss_feed (uuid, user_id, name, url, is_active, is_public, source, torrent_type, auto_detect_catalog, parsing_patterns, filters, created_at, updated_at)
           VALUES (gen_random_uuid()::text, $1, $2, $3, $4, false, $5, $6, $7, $8::json, $9::json, NOW(), NOW())
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
        sqlx::query_scalar::<_, i32>("SELECT id FROM rss_feed WHERE id::text = $1 AND user_id = $2")
            .bind(&feed_id)
            .bind(uid)
            .fetch_optional(&state.pool)
            .await
    } else {
        sqlx::query_scalar::<_, i32>("SELECT id FROM rss_feed WHERE id::text = $1")
            .bind(&feed_id)
            .fetch_optional(&state.pool)
            .await
    };

    let db_id: i32 = match check_result {
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
/// Fetch the feed URL and return a sample item + detected patterns without writing to DB.
pub async fn user_test_rss_feed(
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
                .into_response()
        }
    };
    let user_id_filter: Option<i32> = if role == "admin" { None } else { Some(user_id) };

    // Load feed
    let feed = load_feed(&state, &feed_id, user_id_filter).await;
    let (url, name, patterns) = match feed {
        Some(f) => f,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Feed not found"})),
            )
                .into_response()
        }
    };

    // Fetch XML
    let xml = match state
        .http
        .get(&url)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.text().await.unwrap_or_default(),
        _ => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"status": "error", "message": "Could not fetch feed URL"})),
            )
                .into_response()
        }
    };

    let items = crate::scrapers::rss::parse_rss_xml(&xml);
    let count = items.len();
    if count == 0 {
        return Json(json!({"status": "error", "message": "No items found in feed"}))
            .into_response();
    }

    let empty = serde_json::Value::Object(Default::default());
    let pat = patterns.as_ref().unwrap_or(&empty);
    let first = &items[0];

    // Extract fields from sample item
    let sample_title = first.title.as_deref().unwrap_or("");
    let sample_hash = crate::scrapers::rss::extract_info_hash_pub(first, pat);
    let sample_size = crate::scrapers::rss::extract_size_pub(first, pat);
    let parsed = crate::parser::parse_title(sample_title);

    let sample = json!({
        "title": sample_title,
        "info_hash": sample_hash,
        "size_bytes": sample_size,
        "link": first.link,
        "description": first.description.as_deref().map(|d| &d[..d.len().min(200)]),
        "parsed_title": parsed.title,
        "parsed_year": parsed.year,
        "seasons": parsed.seasons,
        "episodes": parsed.episodes,
    });

    Json(json!({
        "status": "success",
        "message": format!("Successfully fetched feed with {count} items"),
        "items_count": count,
        "feed_name": name,
        "sample_item": sample,
    }))
    .into_response()
}

/// POST /api/v1/user-rss/feeds/test-url
/// Test an arbitrary RSS URL (no DB read).
pub async fn user_test_rss_feed_url(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> impl IntoResponse {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let url = match body.get("url").and_then(|v| v.as_str()) {
        Some(u) => u.to_string(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "url required"})),
            )
                .into_response()
        }
    };

    let xml = match state
        .http
        .get(&url)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.text().await.unwrap_or_default(),
        _ => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"status": "error", "message": "Could not fetch feed URL"})),
            )
                .into_response()
        }
    };

    let items = crate::scrapers::rss::parse_rss_xml(&xml);
    let count = items.len();
    if count == 0 {
        return Json(json!({"status": "error", "message": "No items found in feed"}))
            .into_response();
    }

    let first = &items[0];
    let empty = serde_json::Value::Object(Default::default());
    let pat = body.get("patterns").unwrap_or(&empty);
    let sample_title = first.title.as_deref().unwrap_or("");
    let sample_hash = crate::scrapers::rss::extract_info_hash_pub(first, pat);

    Json(json!({
        "status": "success",
        "message": format!("Successfully fetched feed with {count} items"),
        "items_count": count,
        "sample_item": {
            "title": sample_title,
            "info_hash": sample_hash,
            "link": first.link,
        },
    }))
    .into_response()
}

// ─── Helper: load feed row ────────────────────────────────────────────────────

async fn load_feed(
    state: &AppState,
    feed_id: &str,
    user_id_filter: Option<i32>,
) -> Option<(String, String, Option<serde_json::Value>)> {
    type Row = (String, String, Option<serde_json::Value>); // url, name, parsing_patterns
    if let Some(uid) = user_id_filter {
        sqlx::query_as::<_, Row>(
            "SELECT url, name, parsing_patterns FROM rss_feed WHERE id::text = $1 AND user_id = $2",
        )
        .bind(feed_id)
        .bind(uid)
        .fetch_optional(&state.pool_ro)
        .await
        .ok()
        .flatten()
    } else {
        sqlx::query_as::<_, Row>(
            "SELECT url, name, parsing_patterns FROM rss_feed WHERE id::text = $1",
        )
        .bind(feed_id)
        .fetch_optional(&state.pool_ro)
        .await
        .ok()
        .flatten()
    }
}

/// POST /api/v1/user-rss/feeds/{feed_id}/scrape
pub async fn user_scrape_single_feed(
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
                .into_response()
        }
    };
    let user_id_filter: Option<i32> = if role == "admin" { None } else { Some(user_id) };

    // Load full feed row
    type FeedRow = (
        i32,
        String,
        String,
        Option<String>,
        Option<serde_json::Value>,
        Option<serde_json::Value>,
        bool,
        String,
    );
    let row: Option<FeedRow> = if let Some(uid) = user_id_filter {
        sqlx::query_as(
            "SELECT id, url, name, source, parsing_patterns, filters, auto_detect_catalog, torrent_type::text FROM rss_feed WHERE id::text = $1 AND user_id = $2",
        )
        .bind(&feed_id).bind(uid)
        .fetch_optional(&state.pool_ro).await.ok().flatten()
    } else {
        sqlx::query_as(
            "SELECT id, url, name, source, parsing_patterns, filters, auto_detect_catalog, torrent_type::text FROM rss_feed WHERE id::text = $1",
        )
        .bind(&feed_id)
        .fetch_optional(&state.pool_ro).await.ok().flatten()
    };

    let (db_id, url, name, source, patterns, filters, auto_detect, feed_torrent_type) = match row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Feed not found"})),
            )
                .into_response()
        }
    };

    // Spawn scraping as a background task — returning immediately like Python's async_send()
    let pool = state.pool.clone();
    let http = state.http.clone();
    let tmdb_key = state.config.tmdb_api_key.clone();
    let cinemeta_fallback = state.config.imdb_cinemeta_fallback_enabled;
    let feed_type = crate::scrapers::torrent_metadata::parse_torrent_type_str(&feed_torrent_type);
    tokio::spawn(async move {
        crate::scrapers::rss::scrape_feed(
            &pool,
            &http,
            db_id,
            &url,
            &name,
            source.as_deref(),
            patterns.as_ref(),
            filters.as_ref(),
            auto_detect,
            feed_type,
            tmdb_key.as_deref(),
            cinemeta_fallback,
        )
        .await;
    });

    Json(json!({
        "status": "success",
        "message": "RSS feed scraping started in background",
    }))
    .into_response()
}

/// POST /api/v1/user-rss/feeds/run-all — scrapes all active feeds for this user (admin: all feeds).
pub async fn user_run_all_scrapers(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let (user_id, role) = match validate_token_and_role(&headers, &state.config.secret_key_raw) {
        Some(r) => r,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    type FeedRow = (
        i32,
        String,
        String,
        Option<String>,
        Option<serde_json::Value>,
        Option<serde_json::Value>,
        bool,
        String,
    );
    let feeds: Vec<FeedRow> = if role == "admin" {
        sqlx::query_as(
            "SELECT id, url, name, source, parsing_patterns, filters, auto_detect_catalog, torrent_type::text FROM rss_feed WHERE is_active = true",
        )
        .fetch_all(&state.pool_ro).await.unwrap_or_default()
    } else {
        sqlx::query_as(
            "SELECT id, url, name, source, parsing_patterns, filters, auto_detect_catalog, torrent_type::text FROM rss_feed WHERE is_active = true AND user_id = $1",
        )
        .bind(user_id)
        .fetch_all(&state.pool_ro).await.unwrap_or_default()
    };

    let total = feeds.len();
    let pool = state.pool.clone();
    let http = state.http.clone();
    let tmdb_key = state.config.tmdb_api_key.clone();
    let cinemeta_fallback = state.config.imdb_cinemeta_fallback_enabled;
    tokio::spawn(async move {
        for (db_id, url, name, source, patterns, filters, auto_detect, feed_torrent_type) in feeds {
            let feed_type =
                crate::scrapers::torrent_metadata::parse_torrent_type_str(&feed_torrent_type);
            crate::scrapers::rss::scrape_feed(
                &pool,
                &http,
                db_id,
                &url,
                &name,
                source.as_deref(),
                patterns.as_ref(),
                filters.as_ref(),
                auto_detect,
                feed_type,
                tmdb_key.as_deref(),
                cinemeta_fallback,
            )
            .await;
        }
    });

    Json(json!({
        "status": "success",
        "message": format!("Started scraping {total} active feeds in background"),
    }))
    .into_response()
}

/// POST /api/v1/user-rss/feeds/bulk-status
pub async fn user_bulk_update_feed_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkStatusRequest>,
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

    // Parse feed_ids (stored as strings in the request)
    let ids: Vec<i32> = body
        .feed_ids
        .iter()
        .filter_map(|s| s.parse::<i32>().ok())
        .collect();

    if ids.is_empty() {
        return Json(json!({"detail": "No valid feed IDs provided"})).into_response();
    }

    let result = if role == "admin" {
        sqlx::query("UPDATE rss_feed SET is_active = $1, updated_at = NOW() WHERE id = ANY($2)")
            .bind(body.is_active)
            .bind(&ids)
            .execute(&state.pool)
            .await
    } else {
        sqlx::query(
            "UPDATE rss_feed SET is_active = $1, updated_at = NOW() WHERE id = ANY($2) AND user_id = $3",
        )
        .bind(body.is_active)
        .bind(&ids)
        .bind(user_id)
        .execute(&state.pool)
        .await
    };

    match result {
        Ok(r) => Json(json!({"detail": format!("Updated {} RSS feeds", r.rows_affected())}))
            .into_response(),
        Err(e) => {
            tracing::error!("user_bulk_update_feed_status: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
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
    let enabled = !state.config.disable_rss_feed_scraper;
    Json(json!({
        "crontab": state.config.rss_feed_scraper_crontab,
        "enabled": enabled,
        "next_run": null,
    }))
    .into_response()
}
