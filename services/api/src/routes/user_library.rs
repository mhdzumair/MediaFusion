/// User library and watchlist endpoints.
///
/// Routes (prefix /api/v1/library):
///   GET    /                         → get_library
///   POST   /                         → add_to_library
///   GET    /stats                    → get_library_stats
///   GET    /check/{media_id}         → check_in_library
///   GET    /{item_id}                → get_library_item
///   DELETE /{item_id}                → remove_from_library
///   DELETE /by-media-id/{media_id}  → remove_from_library_by_media_id
///
/// Routes (prefix /api/v1/watchlist):
///   GET    /providers                → get_watchlist_providers
///   GET    /{provider}               → get_watchlist
///   GET    /{provider}/missing       → get_missing_torrents  (stub)
///   POST   /{provider}/import        → import_torrents  (stub)
///   POST   /{provider}/import/advanced → advanced_import  (stub)
///   POST   /{provider}/remove        → remove_torrent  (stub)
///   POST   /{provider}/clear-all     → clear_all_torrents  (stub)

use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use serde::Deserialize;
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helper ──────────────────────────────────────────────────────────────

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

// ─── Query / body structs ─────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct LibraryListQuery {
    pub catalog_type: Option<String>,
    pub search: Option<String>,
    pub external_id: Option<String>,
    #[serde(default = "default_sort")]
    pub sort: String,
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
}

fn default_sort() -> String {
    "added".to_string()
}
fn default_page() -> i64 {
    1
}
fn default_page_size() -> i64 {
    25
}

#[derive(Deserialize)]
pub struct LibraryItemCreate {
    pub media_id: i64,
    pub catalog_type: String,
}

#[derive(Deserialize)]
pub struct WatchlistQuery {
    pub profile_id: Option<i64>,
    pub media_type: Option<String>,
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_watchlist_page_size")]
    pub page_size: i64,
}

fn default_watchlist_page_size() -> i64 {
    25
}

// ─── Helper: fetch external IDs for a media item ──────────────────────────────

async fn get_external_ids(pool: &sqlx::PgPool, media_id: i64) -> serde_json::Value {
    let rows: Vec<(String, String)> = sqlx::query_as(
        "SELECT source, external_id FROM media_external_id WHERE media_id = $1",
    )
    .bind(media_id)
    .fetch_all(pool)
    .await
    .unwrap_or_default();
    let mut map = serde_json::Map::new();
    for (source, id) in rows {
        map.insert(source, serde_json::Value::String(id));
    }
    serde_json::Value::Object(map)
}

async fn get_external_ids_batch(
    pool: &sqlx::PgPool,
    media_ids: &[i64],
) -> std::collections::HashMap<i64, serde_json::Value> {
    if media_ids.is_empty() {
        return std::collections::HashMap::new();
    }
    let rows: Vec<(i64, String, String)> = sqlx::query_as(
        "SELECT media_id, source, external_id FROM media_external_id WHERE media_id = ANY($1)",
    )
    .bind(media_ids)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    let mut map: std::collections::HashMap<i64, serde_json::Map<String, serde_json::Value>> =
        std::collections::HashMap::new();
    for (mid, source, id) in rows {
        map.entry(mid)
            .or_default()
            .insert(source, serde_json::Value::String(id));
    }
    map.into_iter()
        .map(|(k, v)| (k, serde_json::Value::Object(v)))
        .collect()
}

fn build_library_item(
    id: i64,
    media_id: i64,
    catalog_type: &str,
    title: &str,
    poster_cached: Option<&str>,
    added_at: DateTime<Utc>,
    ext_ids: &serde_json::Value,
    host_url: &str,
) -> serde_json::Value {
    let imdb_id = ext_ids
        .get("imdb")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let poster_id = if imdb_id.is_empty() {
        format!("mf:{media_id}")
    } else {
        imdb_id.clone()
    };
    let poster = poster_cached
        .filter(|p| !p.is_empty())
        .map(|p| p.to_string())
        .unwrap_or_else(|| format!("{host_url}/poster/{catalog_type}/{poster_id}.jpg"));

    serde_json::json!({
        "id": id,
        "media_id": media_id,
        "external_ids": ext_ids,
        "catalog_type": catalog_type,
        "title": title,
        "poster": poster,
        "added_at": added_at.to_rfc3339(),
    })
}

// ─── Handlers: User Library ───────────────────────────────────────────────────

/// GET /api/v1/library
pub async fn get_library(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<LibraryListQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let page = params.page.max(1);
    let page_size = params.page_size.clamp(1, 100);
    let offset = (page - 1) * page_size;

    // Resolve external_id filter to a media_id if provided
    let external_id_media: Option<i64> = if let Some(ref eid) = params.external_id {
        match sqlx::query_scalar::<_, i64>(
            "SELECT media_id FROM media_external_id WHERE external_id = $1 LIMIT 1",
        )
        .bind(eid)
        .fetch_optional(&state.pool_ro)
        .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("get_library external_id lookup: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    } else {
        None
    };

    // If external_id was given but not found, return empty
    if params.external_id.is_some() && external_id_media.is_none() {
        return Json(serde_json::json!({
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "has_more": false
        }))
        .into_response();
    }

    // Build count query
    let mut count_sql =
        String::from("SELECT COUNT(*) FROM user_library_item WHERE user_id = $1");
    let mut idx = 2i32;
    if params.catalog_type.is_some() {
        count_sql.push_str(&format!(" AND catalog_type = ${idx}"));
        idx += 1;
    }
    if let Some(mid) = external_id_media {
        count_sql.push_str(&format!(" AND media_id = ${idx}"));
        idx += 1;
        let _ = mid;
    }
    if params.search.is_some() {
        count_sql.push_str(&format!(" AND title_cached ILIKE ${idx}"));
        idx += 1;
    }
    let _ = idx;

    let total: i64 = {
        let mut q = sqlx::query_scalar::<_, i64>(&count_sql).bind(user_id);
        if let Some(ref ct) = params.catalog_type {
            q = q.bind(ct.clone());
        }
        if let Some(mid) = external_id_media {
            q = q.bind(mid);
        }
        if let Some(ref search) = params.search {
            q = q.bind(format!("%{search}%"));
        }
        match q.fetch_one(&state.pool_ro).await {
            Ok(c) => c,
            Err(e) => {
                tracing::error!("get_library count: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    // Build fetch query
    let mut sql = String::from(
        "SELECT id, media_id, catalog_type, title_cached, poster_cached, added_at \
         FROM user_library_item WHERE user_id = $1",
    );
    let mut idx = 2i32;
    if params.catalog_type.is_some() {
        sql.push_str(&format!(" AND catalog_type = ${idx}"));
        idx += 1;
    }
    if external_id_media.is_some() {
        sql.push_str(&format!(" AND media_id = ${idx}"));
        idx += 1;
    }
    if params.search.is_some() {
        sql.push_str(&format!(" AND title_cached ILIKE ${idx}"));
        idx += 1;
    }
    if params.sort == "title" {
        sql.push_str(" ORDER BY title_cached ASC");
    } else {
        sql.push_str(" ORDER BY added_at DESC");
    }
    sql.push_str(&format!(" LIMIT ${idx} OFFSET ${}", idx + 1));

    let rows: Vec<(i64, i64, String, String, Option<String>, DateTime<Utc>)> = {
        let mut q = sqlx::query_as::<
            _,
            (i64, i64, String, String, Option<String>, DateTime<Utc>),
        >(&sql)
        .bind(user_id);
        if let Some(ref ct) = params.catalog_type {
            q = q.bind(ct.clone());
        }
        if let Some(mid) = external_id_media {
            q = q.bind(mid);
        }
        if let Some(ref search) = params.search {
            q = q.bind(format!("%{search}%"));
        }
        q = q.bind(page_size).bind(offset);
        match q.fetch_all(&state.pool_ro).await {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("get_library fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    let media_ids: Vec<i64> = rows.iter().map(|r| r.1).collect();
    let ext_map = get_external_ids_batch(&state.pool_ro, &media_ids).await;

    let items: Vec<serde_json::Value> = rows
        .iter()
        .map(|(id, mid, ct, title, poster, added_at)| {
            let ext = ext_map
                .get(mid)
                .cloned()
                .unwrap_or_else(|| serde_json::Value::Object(serde_json::Map::new()));
            build_library_item(
                *id,
                *mid,
                ct,
                title,
                poster.as_deref(),
                *added_at,
                &ext,
                &state.config.host_url,
            )
        })
        .collect();

    let has_more = (offset + rows.len() as i64) < total;
    Json(serde_json::json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }))
    .into_response()
}

/// GET /api/v1/library/stats
pub async fn get_library_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let total: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM user_library_item WHERE user_id = $1",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let movies: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM user_library_item WHERE user_id = $1 AND catalog_type = 'movie'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let series: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM user_library_item WHERE user_id = $1 AND catalog_type = 'series'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let tv: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM user_library_item WHERE user_id = $1 AND catalog_type = 'tv'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    Json(serde_json::json!({
        "total_items": total,
        "movies": movies,
        "series": series,
        "tv": tv,
    }))
    .into_response()
}

/// POST /api/v1/library
pub async fn add_to_library(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<LibraryItemCreate>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    // Verify media exists
    let media: Option<(i64, String)> =
        match sqlx::query_as("SELECT id, title FROM media WHERE id = $1")
            .bind(body.media_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("add_to_library media check: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    let (_, media_title) = match media {
        Some(m) => m,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Media not found"})),
            )
                .into_response();
        }
    };

    // Check for existing entry
    let existing: Option<i64> = sqlx::query_scalar(
        "SELECT id FROM user_library_item WHERE user_id = $1 AND media_id = $2 LIMIT 1",
    )
    .bind(user_id)
    .bind(body.media_id)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    if existing.is_some() {
        return (
            StatusCode::CONFLICT,
            Json(serde_json::json!({"detail": "Item already in library"})),
        )
            .into_response();
    }

    // Get primary poster
    let poster_cached: Option<String> = sqlx::query_scalar(
        "SELECT url FROM media_image WHERE media_id = $1 AND image_type = 'poster' AND is_primary = true LIMIT 1",
    )
    .bind(body.media_id)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    // Insert
    let row: (i64, i64, String, String, Option<String>, DateTime<Utc>) =
        match sqlx::query_as(
            r#"INSERT INTO user_library_item (user_id, media_id, catalog_type, title_cached, poster_cached, added_at)
               VALUES ($1, $2, $3, $4, $5, NOW())
               RETURNING id, media_id, catalog_type, title_cached, poster_cached, added_at"#,
        )
        .bind(user_id)
        .bind(body.media_id)
        .bind(&body.catalog_type)
        .bind(&media_title)
        .bind(&poster_cached)
        .fetch_one(&state.pool)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("add_to_library insert: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    let ext = get_external_ids(&state.pool, body.media_id).await;
    let item = build_library_item(
        row.0,
        row.1,
        &row.2,
        &row.3,
        row.4.as_deref(),
        row.5,
        &ext,
        &state.config.host_url,
    );
    (StatusCode::CREATED, Json(item)).into_response()
}

/// GET /api/v1/library/{item_id}
pub async fn get_library_item(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(item_id): Path<i64>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let row: Option<(i64, i64, String, String, Option<String>, DateTime<Utc>)> =
        match sqlx::query_as(
            "SELECT id, media_id, catalog_type, title_cached, poster_cached, added_at \
             FROM user_library_item WHERE id = $1 AND user_id = $2",
        )
        .bind(item_id)
        .bind(user_id)
        .fetch_optional(&state.pool_ro)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("get_library_item db: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    match row {
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Library item not found"})),
        )
            .into_response(),
        Some((id, mid, ct, title, poster, added_at)) => {
            let ext = get_external_ids(&state.pool_ro, mid).await;
            Json(build_library_item(
                id,
                mid,
                &ct,
                &title,
                poster.as_deref(),
                added_at,
                &ext,
                &state.config.host_url,
            ))
            .into_response()
        }
    }
}

/// GET /api/v1/library/check/{media_id}
pub async fn check_in_library(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let item_id: Option<i64> = sqlx::query_scalar(
        "SELECT id FROM user_library_item WHERE media_id = $1 AND user_id = $2 LIMIT 1",
    )
    .bind(media_id)
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    Json(serde_json::json!({
        "in_library": item_id.is_some(),
        "item_id": item_id,
    }))
    .into_response()
}

/// DELETE /api/v1/library/{item_id}
pub async fn remove_from_library(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(item_id): Path<i64>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let result = sqlx::query(
        "DELETE FROM user_library_item WHERE id = $1 AND user_id = $2",
    )
    .bind(item_id)
    .bind(user_id)
    .execute(&state.pool)
    .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Library item not found"})),
        )
            .into_response(),
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("remove_from_library: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// DELETE /api/v1/library/by-media-id/{media_id}
pub async fn remove_from_library_by_media_id(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let result = sqlx::query(
        "DELETE FROM user_library_item WHERE media_id = $1 AND user_id = $2",
    )
    .bind(media_id)
    .bind(user_id)
    .execute(&state.pool)
    .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Library item not found"})),
        )
            .into_response(),
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("remove_from_library_by_media_id: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

// ─── Handlers: Watchlist (debrid provider integrations) ───────────────────────
// NOTE: The full watchlist import/remove flow requires deep integration with the
// Python debrid provider mapper (streaming_providers::mapper). These endpoints
// provide the structural Rust stubs that proxy complex operations back to the
// Python layer or return appropriate responses for the cases we can serve
// entirely from the database.

/// GET /api/v1/watchlist/providers
pub async fn get_watchlist_providers(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<serde_json::Value>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let profile_id: Option<i64> = params
        .get("profile_id")
        .and_then(|v| v.as_i64());

    // Find the profile — prefer explicit profile_id, fall back to default
    let profile: Option<(i32, serde_json::Value, Option<String>)> = if let Some(pid) = profile_id {
        match sqlx::query_as(
            "SELECT id, config, encrypted_secrets FROM user_profiles WHERE id = $1 AND user_id = $2",
        )
        .bind(pid as i32)
        .bind(user_id)
        .fetch_optional(&state.pool_ro)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("get_watchlist_providers profile fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    } else {
        match sqlx::query_as(
            "SELECT id, config, encrypted_secrets FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1",
        )
        .bind(user_id)
        .fetch_optional(&state.pool_ro)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("get_watchlist_providers default profile fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    let (profile_id_val, config) = match profile {
        None => {
            return Json(serde_json::json!({"providers": [], "profile_id": 0})).into_response();
        }
        Some((pid, cfg, _enc)) => (pid, cfg),
    };

    // Extract providers from the config that have watchlist_providers field
    let watchlist_providers = config
        .get("watchlist_providers")
        .or_else(|| config.get("wp"))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let providers: Vec<serde_json::Value> = watchlist_providers
        .iter()
        .filter_map(|p| {
            let service = p.get("service").or_else(|| p.get("sv"))?.as_str()?;
            Some(serde_json::json!({
                "service": service,
                "name": p.get("name").or_else(|| p.get("n")).and_then(|v| v.as_str()),
                "supports_watchlist": true,
            }))
        })
        .collect();

    Json(serde_json::json!({
        "providers": providers,
        "profile_id": profile_id_val,
    }))
    .into_response()
}

/// GET /api/v1/watchlist/{provider}
pub async fn get_watchlist(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    Query(params): Query<WatchlistQuery>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    // This endpoint requires the Python debrid provider integration to fetch
    // the user's downloaded info hashes. Proxy to Python if available.
    if let Some(ref python_url) = state.config.python_proxy_url {
        let auth = headers
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let mut url = format!("{python_url}/api/v1/watchlist/{provider}");
        let mut query_parts = Vec::new();
        if let Some(pid) = params.profile_id {
            query_parts.push(format!("profile_id={pid}"));
        }
        if let Some(ref mt) = params.media_type {
            query_parts.push(format!("media_type={mt}"));
        }
        query_parts.push(format!("page={}", params.page));
        query_parts.push(format!("page_size={}", params.page_size));
        if !query_parts.is_empty() {
            url.push('?');
            url.push_str(&query_parts.join("&"));
        }
        match state
            .http
            .get(&url)
            .header("Authorization", auth)
            .send()
            .await
        {
            Ok(resp) => {
                let status = StatusCode::from_u16(resp.status().as_u16())
                    .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
                let body = resp.bytes().await.unwrap_or_default();
                return (
                    status,
                    axum::response::AppendHeaders([(
                        axum::http::header::CONTENT_TYPE,
                        "application/json",
                    )]),
                    body,
                )
                    .into_response();
            }
            Err(e) => {
                tracing::error!("get_watchlist proxy: {e}");
                return StatusCode::BAD_GATEWAY.into_response();
            }
        }
    }

    // No Python proxy — return empty response with explanation
    Json(serde_json::json!({
        "items": [],
        "total": 0,
        "page": params.page,
        "page_size": params.page_size,
        "has_more": false,
        "provider": provider,
        "provider_name": null,
    }))
    .into_response()
}

/// GET /api/v1/watchlist/{provider}/missing
pub async fn get_missing_torrents(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    Query(params): Query<serde_json::Value>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    proxy_or_stub(&state, &headers, &format!("/api/v1/watchlist/{provider}/missing"), &params, || {
        serde_json::json!({"items": [], "total": 0, "provider": provider})
    })
    .await
}

/// POST /api/v1/watchlist/{provider}/import
pub async fn import_torrents(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    body: axum::body::Bytes,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    proxy_post_or_stub(
        &state,
        &headers,
        &format!("/api/v1/watchlist/{provider}/import"),
        body,
        || serde_json::json!({"imported": 0, "failed": 0, "skipped": 0, "details": []}),
    )
    .await
}

/// POST /api/v1/watchlist/{provider}/import/advanced
pub async fn advanced_import_torrents(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    body: axum::body::Bytes,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    proxy_post_or_stub(
        &state,
        &headers,
        &format!("/api/v1/watchlist/{provider}/import/advanced"),
        body,
        || serde_json::json!({"imported": 0, "failed": 0, "skipped": 0, "details": []}),
    )
    .await
}

/// POST /api/v1/watchlist/{provider}/remove
pub async fn remove_torrent_from_debrid(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    body: axum::body::Bytes,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    proxy_post_or_stub(
        &state,
        &headers,
        &format!("/api/v1/watchlist/{provider}/remove"),
        body,
        || serde_json::json!({"success": false, "message": "Not implemented in Rust layer"}),
    )
    .await
}

/// POST /api/v1/watchlist/{provider}/clear-all
pub async fn clear_all_torrents_from_debrid(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    body: axum::body::Bytes,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    proxy_post_or_stub(
        &state,
        &headers,
        &format!("/api/v1/watchlist/{provider}/clear-all"),
        body,
        || serde_json::json!({"success": false, "message": "Not implemented in Rust layer"}),
    )
    .await
}

// ─── Proxy helpers ────────────────────────────────────────────────────────────

async fn proxy_or_stub(
    state: &AppState,
    headers: &HeaderMap,
    path: &str,
    _params: &serde_json::Value,
    stub: impl Fn() -> serde_json::Value,
) -> Response {
    if let Some(ref python_url) = state.config.python_proxy_url {
        let auth = headers
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let url = format!("{python_url}{path}");
        match state.http.get(&url).header("Authorization", auth).send().await {
            Ok(resp) => {
                let status = StatusCode::from_u16(resp.status().as_u16())
                    .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
                let body = resp.bytes().await.unwrap_or_default();
                return (
                    status,
                    axum::response::AppendHeaders([(
                        axum::http::header::CONTENT_TYPE,
                        "application/json",
                    )]),
                    body,
                )
                    .into_response();
            }
            Err(e) => {
                tracing::error!("proxy GET {path}: {e}");
                return StatusCode::BAD_GATEWAY.into_response();
            }
        }
    }
    Json(stub()).into_response()
}

async fn proxy_post_or_stub(
    state: &AppState,
    headers: &HeaderMap,
    path: &str,
    body: axum::body::Bytes,
    stub: impl Fn() -> serde_json::Value,
) -> Response {
    if let Some(ref python_url) = state.config.python_proxy_url {
        let auth = headers
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let url = format!("{python_url}{path}");
        match state
            .http
            .post(&url)
            .header("Authorization", auth)
            .header("Content-Type", "application/json")
            .body(body)
            .send()
            .await
        {
            Ok(resp) => {
                let status = StatusCode::from_u16(resp.status().as_u16())
                    .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
                let resp_body = resp.bytes().await.unwrap_or_default();
                return (
                    status,
                    axum::response::AppendHeaders([(
                        axum::http::header::CONTENT_TYPE,
                        "application/json",
                    )]),
                    resp_body,
                )
                    .into_response();
            }
            Err(e) => {
                tracing::error!("proxy POST {path}: {e}");
                return StatusCode::BAD_GATEWAY.into_response();
            }
        }
    }
    Json(stub()).into_response()
}

// ─── Aliases for mod.rs compatibility ────────────────────────────────────────

pub use get_library as list_library;
pub use check_in_library as get_library_status;

pub async fn bulk_library_operation(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> impl IntoResponse {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"detail": "Unauthorized"}))).into_response()
        }
    };
    if let Some(py_url) = &state.config.python_proxy_url {
        let url = format!("{py_url}/api/v1/library/bulk");
        match state.http.post(&url).json(&body).send().await {
            Ok(r) => {
                let status = StatusCode::from_u16(r.status().as_u16()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
                let resp_body: serde_json::Value = r.json().await.unwrap_or(serde_json::json!({}));
                return (status, Json(resp_body)).into_response();
            }
            Err(e) => {
                tracing::error!("bulk_library_operation proxy: {e}");
                return StatusCode::BAD_GATEWAY.into_response();
            }
        }
    }
    let _ = user_id;
    (StatusCode::NOT_IMPLEMENTED, Json(serde_json::json!({"detail": "Not implemented"}))).into_response()
}
