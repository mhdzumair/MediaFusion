/// Admin keyword filter management endpoints.
///
/// Routes:
///   GET  /api/v1/admin/keyword-filters              → list_keyword_filters
///   POST /api/v1/admin/keyword-filters              → add_keyword_filter
///   POST /api/v1/admin/keyword-filters/reload       → reload_keyword_cache
///   PATCH /api/v1/admin/keyword-filters/{id}        → toggle_keyword_filter
///   DELETE /api/v1/admin/keyword-filters/{id}       → delete_keyword_filter
///   GET  /api/v1/admin/keyword-whitelist            → list_keyword_whitelist
///   POST /api/v1/admin/keyword-whitelist            → add_whitelist_phrase
///   DELETE /api/v1/admin/keyword-whitelist/{id}     → delete_whitelist_phrase
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::Sha256;

use crate::state::{load_keyword_filter_cache, AppState};

// ─── Auth helpers ─────────────────────────────────────────────────────────────

fn validate_admin(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
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
    if data["role"].as_str() != Some("admin") {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

async fn check_admin_role(pool: &sqlx::PgPool, user_id: i32) -> bool {
    let role: Option<String> =
        sqlx::query_scalar("SELECT LOWER(role::text) FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);
    role.as_deref() == Some("admin")
}

// ─── Query params ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct KeywordListQuery {
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
    pub search: Option<String>,
}

#[derive(Deserialize)]
pub struct WhitelistListQuery {
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
}

fn default_page() -> i64 {
    1
}
fn default_page_size() -> i64 {
    50
}

// ─── Request / Response structs ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct AddKeywordRequest {
    pub keyword: String,
}

#[derive(Deserialize)]
pub struct AddPhraseRequest {
    pub phrase: String,
    pub reason: Option<String>,
}

#[derive(Deserialize)]
pub struct ToggleRequest {
    pub is_active: bool,
}

#[derive(Serialize, sqlx::FromRow)]
pub struct KeywordFilterRow {
    pub id: i32,
    pub keyword: String,
    pub is_active: bool,
    pub created_at: DateTime<Utc>,
}

#[derive(Serialize, sqlx::FromRow)]
pub struct WhitelistRow {
    pub id: i32,
    pub phrase: String,
    pub reason: Option<String>,
    pub created_at: DateTime<Utc>,
}

// ─── Reload cache helper ──────────────────────────────────────────────────────

async fn reload_cache(state: &AppState) {
    let new_cache = load_keyword_filter_cache(&state.pool).await;
    if let Ok(mut w) = state.keyword_filters.write() {
        *w = new_cache;
    }
}

// ─── Handlers ────────────────────────────────────────────────────────────────

/// GET /api/v1/admin/keyword-filters?page=1&page_size=50&search=xxx
pub async fn list_keyword_filters(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(q): Query<KeywordListQuery>,
) -> impl IntoResponse {
    let user_id = match validate_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    if !check_admin_role(&state.pool, user_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Admin role required"})),
        )
            .into_response();
    }

    let page = q.page.max(1);
    let page_size = q.page_size.clamp(1, 500);
    let offset = (page - 1) * page_size;

    let (items, total) = if let Some(ref search) = q.search {
        let pattern = format!("%{}%", search.to_lowercase());
        let total: i64 =
            sqlx::query_scalar("SELECT COUNT(*) FROM keyword_filters WHERE LOWER(keyword) LIKE $1")
                .bind(&pattern)
                .fetch_one(&state.pool)
                .await
                .unwrap_or(0);
        let items: Vec<KeywordFilterRow> = sqlx::query_as(
            "SELECT id, keyword, is_active, created_at FROM keyword_filters WHERE LOWER(keyword) LIKE $1 ORDER BY keyword LIMIT $2 OFFSET $3",
        )
        .bind(&pattern)
        .bind(page_size)
        .bind(offset)
        .fetch_all(&state.pool)
        .await
        .unwrap_or_default();
        (items, total)
    } else {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM keyword_filters")
            .fetch_one(&state.pool)
            .await
            .unwrap_or(0);
        let items: Vec<KeywordFilterRow> = sqlx::query_as(
            "SELECT id, keyword, is_active, created_at FROM keyword_filters ORDER BY keyword LIMIT $1 OFFSET $2",
        )
        .bind(page_size)
        .bind(offset)
        .fetch_all(&state.pool)
        .await
        .unwrap_or_default();
        (items, total)
    };

    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }))
    .into_response()
}

/// POST /api/v1/admin/keyword-filters  body: {keyword}
pub async fn add_keyword_filter(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<AddKeywordRequest>,
) -> impl IntoResponse {
    let user_id = match validate_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    if !check_admin_role(&state.pool, user_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Admin role required"})),
        )
            .into_response();
    }

    let keyword = body.keyword.trim().to_lowercase();
    if keyword.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "keyword must not be empty"})),
        )
            .into_response();
    }

    let result: Result<KeywordFilterRow, sqlx::Error> = sqlx::query_as(
        "INSERT INTO keyword_filters (keyword) VALUES ($1) RETURNING id, keyword, is_active, created_at",
    )
    .bind(&keyword)
    .fetch_one(&state.pool)
    .await;

    match result {
        Ok(row) => {
            reload_cache(&state).await;
            (StatusCode::CREATED, Json(json!(row))).into_response()
        }
        Err(e) => {
            let msg = e.to_string();
            if msg.to_lowercase().contains("unique") || msg.to_lowercase().contains("duplicate") {
                (
                    StatusCode::CONFLICT,
                    Json(json!({"detail": "keyword already exists"})),
                )
                    .into_response()
            } else {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"detail": msg})),
                )
                    .into_response()
            }
        }
    }
}

/// PATCH /api/v1/admin/keyword-filters/{id}  body: {is_active}
pub async fn toggle_keyword_filter(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
    Json(body): Json<ToggleRequest>,
) -> impl IntoResponse {
    let user_id = match validate_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    if !check_admin_role(&state.pool, user_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Admin role required"})),
        )
            .into_response();
    }

    let result: Option<KeywordFilterRow> = sqlx::query_as(
        "UPDATE keyword_filters SET is_active = $1 WHERE id = $2 RETURNING id, keyword, is_active, created_at",
    )
    .bind(body.is_active)
    .bind(id)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    match result {
        Some(row) => {
            reload_cache(&state).await;
            Json(json!(row)).into_response()
        }
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "keyword filter not found"})),
        )
            .into_response(),
    }
}

/// DELETE /api/v1/admin/keyword-filters/{id}
pub async fn delete_keyword_filter(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> impl IntoResponse {
    let user_id = match validate_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    if !check_admin_role(&state.pool, user_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Admin role required"})),
        )
            .into_response();
    }

    let result = sqlx::query("DELETE FROM keyword_filters WHERE id = $1")
        .bind(id)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) if r.rows_affected() > 0 => {
            reload_cache(&state).await;
            StatusCode::NO_CONTENT.into_response()
        }
        Ok(_) => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "keyword filter not found"})),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": e.to_string()})),
        )
            .into_response(),
    }
}

/// GET /api/v1/admin/keyword-whitelist?page=1&page_size=50
pub async fn list_keyword_whitelist(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(q): Query<WhitelistListQuery>,
) -> impl IntoResponse {
    let user_id = match validate_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    if !check_admin_role(&state.pool, user_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Admin role required"})),
        )
            .into_response();
    }

    let page = q.page.max(1);
    let page_size = q.page_size.clamp(1, 500);
    let offset = (page - 1) * page_size;

    let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM keyword_whitelist")
        .fetch_one(&state.pool)
        .await
        .unwrap_or(0);

    let items: Vec<WhitelistRow> = sqlx::query_as(
        "SELECT id, phrase, reason, created_at FROM keyword_whitelist ORDER BY phrase LIMIT $1 OFFSET $2",
    )
    .bind(page_size)
    .bind(offset)
    .fetch_all(&state.pool)
    .await
    .unwrap_or_default();

    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }))
    .into_response()
}

/// POST /api/v1/admin/keyword-whitelist  body: {phrase, reason?}
pub async fn add_whitelist_phrase(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<AddPhraseRequest>,
) -> impl IntoResponse {
    let user_id = match validate_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    if !check_admin_role(&state.pool, user_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Admin role required"})),
        )
            .into_response();
    }

    let phrase = body.phrase.trim().to_lowercase();
    if phrase.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "phrase must not be empty"})),
        )
            .into_response();
    }

    let result: Result<WhitelistRow, sqlx::Error> = sqlx::query_as(
        "INSERT INTO keyword_whitelist (phrase, reason) VALUES ($1, $2) RETURNING id, phrase, reason, created_at",
    )
    .bind(&phrase)
    .bind(&body.reason)
    .fetch_one(&state.pool)
    .await;

    match result {
        Ok(row) => {
            reload_cache(&state).await;
            (StatusCode::CREATED, Json(json!(row))).into_response()
        }
        Err(e) => {
            let msg = e.to_string();
            if msg.to_lowercase().contains("unique") || msg.to_lowercase().contains("duplicate") {
                (
                    StatusCode::CONFLICT,
                    Json(json!({"detail": "phrase already exists"})),
                )
                    .into_response()
            } else {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"detail": msg})),
                )
                    .into_response()
            }
        }
    }
}

/// DELETE /api/v1/admin/keyword-whitelist/{id}
pub async fn delete_whitelist_phrase(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> impl IntoResponse {
    let user_id = match validate_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    if !check_admin_role(&state.pool, user_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Admin role required"})),
        )
            .into_response();
    }

    let result = sqlx::query("DELETE FROM keyword_whitelist WHERE id = $1")
        .bind(id)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) if r.rows_affected() > 0 => {
            reload_cache(&state).await;
            StatusCode::NO_CONTENT.into_response()
        }
        Ok(_) => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "whitelist phrase not found"})),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": e.to_string()})),
        )
            .into_response(),
    }
}

/// POST /api/v1/admin/keyword-filters/reload
pub async fn reload_keyword_cache(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let user_id = match validate_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    if !check_admin_role(&state.pool, user_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Admin role required"})),
        )
            .into_response();
    }

    let new_cache = load_keyword_filter_cache(&state.pool).await;
    let keywords_count = new_cache.keywords.len();
    let whitelist_count = new_cache.whitelist.len();
    if let Ok(mut w) = state.keyword_filters.write() {
        *w = new_cache;
    }

    Json(json!({
        "keywords_count": keywords_count,
        "whitelist_count": whitelist_count,
    }))
    .into_response()
}
