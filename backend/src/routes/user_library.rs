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
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use sha2::Sha256;

use serde_json::json;

use crate::{
    db::{self, MediaId},
    state::AppState,
};

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

async fn get_external_ids(pool: &sqlx::PgPool, media_id: i32) -> serde_json::Value {
    let rows: Vec<(String, String)> =
        sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
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
    let media_ids_i32: Vec<i32> = media_ids.iter().map(|&x| x as i32).collect();
    let rows: Vec<(i32, String, String)> = sqlx::query_as(
        "SELECT media_id, provider, external_id FROM media_external_id WHERE media_id = ANY($1)",
    )
    .bind(&media_ids_i32)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    let mut map: std::collections::HashMap<i64, serde_json::Map<String, serde_json::Value>> =
        std::collections::HashMap::new();
    for (mid, source, id) in rows {
        map.entry(mid as i64)
            .or_default()
            .insert(source, serde_json::Value::String(id));
    }
    map.into_iter()
        .map(|(k, v)| (k, serde_json::Value::Object(v)))
        .collect()
}

#[allow(clippy::too_many_arguments)]
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
#[allow(clippy::type_complexity)]
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
    let external_id_media: Option<i32> = if let Some(ref eid) = params.external_id {
        match sqlx::query_scalar::<_, i32>(
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
    let mut count_sql = String::from("SELECT COUNT(*) FROM user_library_item WHERE user_id = $1");
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
        let mut q = sqlx::query_scalar::<_, i64>(&count_sql).bind(user_id as i32);
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

    let rows: Vec<(i32, i32, String, String, Option<String>, DateTime<Utc>)> = {
        let mut q =
            sqlx::query_as::<_, (i32, i32, String, String, Option<String>, DateTime<Utc>)>(&sql)
                .bind(user_id as i32);
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

    let media_ids: Vec<i64> = rows.iter().map(|r| r.1 as i64).collect();
    let ext_map = get_external_ids_batch(&state.pool_ro, &media_ids).await;

    let items: Vec<serde_json::Value> = rows
        .iter()
        .map(|(id, mid, ct, title, poster, added_at)| {
            let ext = ext_map
                .get(&(*mid as i64))
                .cloned()
                .unwrap_or_else(|| serde_json::Value::Object(serde_json::Map::new()));
            build_library_item(
                *id as i64,
                *mid as i64,
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
pub async fn get_library_stats(headers: HeaderMap, State(state): State<Arc<AppState>>) -> Response {
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

    let total: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM user_library_item WHERE user_id = $1")
            .bind(user_id as i32)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

    let movies: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM user_library_item WHERE user_id = $1 AND catalog_type = 'MOVIE'",
    )
    .bind(user_id as i32)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let series: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM user_library_item WHERE user_id = $1 AND catalog_type = 'SERIES'",
    )
    .bind(user_id as i32)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let tv: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM user_library_item WHERE user_id = $1 AND catalog_type = 'TV'",
    )
    .bind(user_id as i32)
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
    let media: Option<(i32, String)> =
        match sqlx::query_as("SELECT id, title FROM media WHERE id = $1")
            .bind(body.media_id as i32)
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
    let existing: Option<i32> = sqlx::query_scalar(
        "SELECT id FROM user_library_item WHERE user_id = $1 AND media_id = $2 LIMIT 1",
    )
    .bind(user_id as i32)
    .bind(body.media_id as i32)
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
    let row: (i32, i32, String, String, Option<String>, DateTime<Utc>) =
        match sqlx::query_as(
            r#"INSERT INTO user_library_item (user_id, media_id, catalog_type, title_cached, poster_cached, added_at)
               VALUES ($1, $2, $3, $4, $5, NOW())
               RETURNING id, media_id, catalog_type, title_cached, poster_cached, added_at"#,
        )
        .bind(user_id as i32)
        .bind(body.media_id as i32)
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

    let ext = get_external_ids(&state.pool, body.media_id as i32).await;
    let item = build_library_item(
        row.0 as i64,
        row.1 as i64,
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
#[allow(clippy::type_complexity)]
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
        .bind(user_id as i32)
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
            let ext = get_external_ids(&state.pool_ro, mid as i32).await;
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
    Path(media_id): Path<MediaId>,
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
    .bind(i32::from(media_id))
    .bind(user_id as i32)
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

    let result = sqlx::query("DELETE FROM user_library_item WHERE id = $1 AND user_id = $2")
        .bind(item_id)
        .bind(user_id as i32)
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
    Path(media_id): Path<MediaId>,
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

    let result = sqlx::query("DELETE FROM user_library_item WHERE media_id = $1 AND user_id = $2")
        .bind(i32::from(media_id))
        .bind(user_id as i32)
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

/// Extract all enabled streaming providers from a profile config (both `sps`/`streaming_providers`
/// multi-array and legacy `sp`/`streaming_provider` single-object forms).
pub fn extract_streaming_providers(config: &serde_json::Value) -> Vec<serde_json::Value> {
    let mut result = Vec::new();

    let arr = config
        .get("sps")
        .or_else(|| config.get("streaming_providers"))
        .and_then(|v| v.as_array());

    if let Some(sps) = arr {
        for sp in sps {
            let service = match sp
                .get("sv")
                .or_else(|| sp.get("service"))
                .and_then(|v| v.as_str())
            {
                Some(s) if !s.is_empty() => s,
                _ => continue,
            };
            let enabled = sp
                .get("en")
                .or_else(|| sp.get("enabled"))
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            if enabled {
                let display_name = sp
                    .get("n")
                    .or_else(|| sp.get("name"))
                    .and_then(|v| v.as_str())
                    .unwrap_or(service);
                result.push(serde_json::json!({
                    "service": service,
                    "name": display_name,
                    "enabled": true,
                }));
            }
        }
        return result;
    }

    // Legacy single-provider fallback
    if let Some(sp) = config
        .get("sp")
        .or_else(|| config.get("streaming_provider"))
    {
        let service = match sp
            .get("sv")
            .or_else(|| sp.get("service"))
            .and_then(|v| v.as_str())
        {
            Some(s) if !s.is_empty() => s,
            _ => return result,
        };
        let display_name = sp
            .get("n")
            .or_else(|| sp.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or(service);
        result.push(serde_json::json!({
            "service": service,
            "name": display_name,
            "enabled": true,
        }));
    }

    result
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

    // Return empty response — watchlist fetching not yet implemented natively
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

// ─── Helpers for watchlist / debrid import ───────────────────────────────────

#[derive(Deserialize)]
pub struct MissingQuery {
    profile_id: Option<i32>,
}

/// Fetch the decrypted profile config for the given user and optional profile_id.
async fn get_profile_config(
    pool: &sqlx::PgPool,
    user_id: i64,
    profile_id: Option<i32>,
    secret_key: &[u8; 32],
) -> Option<serde_json::Value> {
    type Row = (Option<serde_json::Value>, Option<String>);
    let row: Option<Row> = if let Some(pid) = profile_id {
        sqlx::query_as::<_, Row>(
            "SELECT config, encrypted_secrets FROM user_profiles WHERE id = $1 AND user_id = $2",
        )
        .bind(pid)
        .bind(user_id as i32)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    } else {
        sqlx::query_as::<_, Row>(
            "SELECT config, encrypted_secrets FROM user_profiles WHERE user_id = $1 AND is_default = true",
        )
        .bind(user_id as i32)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    };

    let (config, encrypted_secrets) = row?;
    let mut full_config: serde_json::Value = config.unwrap_or_else(|| json!({}));
    if let Some(enc) = encrypted_secrets {
        let secrets = crate::crypto::profile::decrypt_secrets(&enc, secret_key);
        crate::crypto::profile::merge_secrets(&mut full_config, &secrets);
    }
    Some(full_config)
}

/// Extract video files from a raw torrent JSON object, handling per-provider field names.
fn extract_video_files(
    raw: &serde_json::Value,
    provider: &str,
    video_extensions: &[&str],
    sample_re: &regex::Regex,
) -> Vec<serde_json::Value> {
    let files_val = match raw.get("files") {
        Some(v) => v,
        None => return vec![],
    };

    match provider {
        "realdebrid" => {
            // Files: [{path, bytes, selected}] — only selected == 1
            files_val
                .as_array()
                .map(|arr| {
                    arr.iter()
                        .filter(|f| f.get("selected").and_then(|s| s.as_i64()).unwrap_or(0) == 1)
                        .filter_map(|f| {
                            let path = f.get("path")?.as_str()?;
                            let size = f.get("bytes").and_then(|v| v.as_i64()).unwrap_or(0);
                            is_wanted_video(path, video_extensions, sample_re)
                                .then(|| json!({"path": path, "size": size}))
                        })
                        .collect()
                })
                .unwrap_or_default()
        }
        "torbox" => {
            // Files: [{short_name/name, size}]
            files_val
                .as_array()
                .map(|arr| {
                    arr.iter()
                        .filter_map(|f| {
                            let path = f
                                .get("short_name")
                                .or_else(|| f.get("name"))
                                .and_then(|v| v.as_str())?;
                            let size = f.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
                            is_wanted_video(path, video_extensions, sample_re)
                                .then(|| json!({"path": path, "size": size}))
                        })
                        .collect()
                })
                .unwrap_or_default()
        }
        "alldebrid" => {
            // Files: nested tree with {n, s, e, l} — flatten recursively
            let mut flat: Vec<(String, i64)> = Vec::new();
            flatten_ad_files_simple(files_val, &mut flat);
            flat.into_iter()
                .filter_map(|(path, size)| {
                    is_wanted_video(&path, video_extensions, sample_re)
                        .then(|| json!({"path": path, "size": size}))
                })
                .collect()
        }
        "debridlink" | "premiumize" | "offcloud" | "pikpak" | "seedr" => {
            // Files: [{name, size}]
            files_val
                .as_array()
                .map(|arr| {
                    arr.iter()
                        .filter_map(|f| {
                            let path = f.get("name").and_then(|v| v.as_str())?;
                            let size = f.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
                            is_wanted_video(path, video_extensions, sample_re)
                                .then(|| json!({"path": path, "size": size}))
                        })
                        .collect()
                })
                .unwrap_or_default()
        }
        _ => vec![],
    }
}

fn is_wanted_video(path: &str, video_extensions: &[&str], sample_re: &regex::Regex) -> bool {
    let path_lc = path.to_lowercase();
    let is_video = video_extensions.iter().any(|e| path_lc.ends_with(e));
    let filename = path.rsplit('/').next().unwrap_or(path);
    is_video && !sample_re.is_match(filename)
}

/// Recursively flatten the AllDebrid nested file tree into (name, size) pairs.
fn flatten_ad_files_simple(node: &serde_json::Value, out: &mut Vec<(String, i64)>) {
    match node {
        serde_json::Value::Array(arr) => {
            for item in arr {
                flatten_ad_files_simple(item, out);
            }
        }
        serde_json::Value::Object(_) => {
            if node.get("l").is_some() {
                // Leaf file
                let name = node
                    .get("n")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let size = node.get("s").and_then(|v| v.as_i64()).unwrap_or(0);
                out.push((name, size));
            } else if let Some(entries) = node.get("e") {
                flatten_ad_files_simple(entries, out);
            }
        }
        _ => {}
    }
}

/// Parsed torrent metadata for the missing-import flow.
struct TorrentMeta {
    title: Option<String>,
    year: Option<i32>,
    /// "movie" | "series" | "sports" — the *display* type shown in the UI.
    parsed_type: String,
    /// DB media type to search against: "movie" | "series".
    db_type: String,
    /// Title to use for the DB full-text search (may differ from the display title).
    search_title: Option<String>,
    /// Detected sports category key (e.g. "formula_racing"), `None` for non-sports.
    sports_category: Option<String>,
}

/// Parse torrent name into metadata. `video_file_count` is the number of video
/// files already extracted — >3 files implies a series.
fn parse_torrent_meta(name: &str, video_file_count: usize) -> TorrentMeta {
    // Racing (F1/F2/F3, MotoGP) is stored as a *series*; the series title is
    // "{league} {event} {year}" and the session is the episode.
    if let Some(cat) = crate::parser::detect_sports_category(name) {
        if matches!(cat, "formula_racing" | "motogp_racing") {
            if let Some(racing) = crate::parser::parse_racing_title(name) {
                return TorrentMeta {
                    title: Some(racing.series_title.clone()),
                    year: racing.year,
                    parsed_type: "sports".to_string(),
                    db_type: "series".to_string(),
                    search_title: Some(racing.series_title),
                    sports_category: Some(cat.to_string()),
                };
            }
        }

        // Other sports → stored as a movie. Strip the date so FTS can match.
        let sports = crate::parser::parse_sports_title(name);
        let title = sports
            .title
            .or_else(|| crate::parser::parse_title(name).title);
        let search = title.as_deref().map(strip_date_tokens);
        return TorrentMeta {
            title,
            year: sports.year,
            parsed_type: "sports".to_string(),
            db_type: "movie".to_string(),
            search_title: search,
            sports_category: Some(cat.to_string()),
        };
    }

    let parsed = crate::parser::parse_title(name);
    let is_series =
        !parsed.seasons.is_empty() || !parsed.episodes.is_empty() || video_file_count > 3;
    let parsed_type = if is_series { "series" } else { "movie" };
    TorrentMeta {
        search_title: parsed.title.clone(),
        title: parsed.title,
        year: parsed.year,
        parsed_type: parsed_type.to_string(),
        db_type: parsed_type.to_string(),
        sports_category: None,
    }
}

/// Derive the racing session/episode title (e.g. "Qualifying") from a file path.
/// Returns `None` when no session can be identified.
fn racing_file_episode_title(path: &str) -> Option<String> {
    let basename = path.rsplit('/').next().unwrap_or(path);
    crate::parser::parse_racing_title(basename).and_then(|r| r.session)
}

/// Strip DD MM YYYY date tokens from a title so FTS can match DB entries.
/// e.g. "WWE Raw 23 05 2026" → "WWE Raw"
fn strip_date_tokens(title: &str) -> String {
    static DATE_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = DATE_RE.get_or_init(|| regex::Regex::new(r"\s+\d{1,2}\s+\d{1,2}\s+\d{4}\b").unwrap());
    let s = re.replace_all(title, "").trim().to_string();
    if s.is_empty() {
        title.to_string()
    } else {
        s
    }
}

/// Find the best-matching media in the DB for `title`/`year`/`media_type`.
/// Returns `(matched_title, external_ids_json)` — both are `None` when no confident match found.
async fn find_metadata_match(
    pool: &sqlx::PgPool,
    title: &str,
    year: Option<i32>,
    media_type: &str,
) -> (Option<String>, Option<serde_json::Value>) {
    let db_type = if media_type == "series" {
        "series"
    } else {
        "movie"
    };
    let candidates = crate::db::search_media_candidates(pool, db_type, title, None).await;
    if candidates.is_empty() {
        return (None, None);
    }

    // Dynamic threshold mirrors the Python implementation.
    let compact_len = title
        .to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric())
        .count();
    let min_score: i32 = if compact_len <= 4 {
        90
    } else if compact_len <= 8 {
        80
    } else {
        68
    };

    let mut best_score = -1i32;
    let mut best_title: Option<String> = None;
    let mut best_ext: Option<serde_json::Value> = None;

    for c in &candidates {
        // Skip candidates with no external IDs (mirrors Python build_missing_external_ids).
        if c.imdb_id.is_none() && c.tmdb_id.is_none() && c.tvdb_id.is_none() {
            continue;
        }

        let sim = crate::parser::similarity_ratio(title, &c.title) as i32;
        if sim < min_score {
            continue;
        }

        let mut score = sim;
        if let Some(y) = year {
            if let Some(cy) = c.year {
                if cy == y {
                    score += 8;
                } else if (cy - y).abs() <= 1 {
                    score += 2;
                }
            }
        }
        if c.imdb_id.is_some() {
            score += 2;
        }
        if c.tmdb_id.is_some() {
            score += 1;
        }

        if score > best_score {
            best_score = score;
            best_title = Some(c.title.clone());
            best_ext = Some(json!({
                "imdb": c.imdb_id,
                "tmdb": c.tmdb_id,
                "tvdb": c.tvdb_id,
            }));
        }
    }

    (best_title, best_ext)
}

/// Extract the token for a named provider from a decrypted profile config.
fn extract_provider_token(config: &serde_json::Value, provider: &str) -> Option<String> {
    let sps = config
        .get("sps")
        .or_else(|| config.get("streaming_providers"))
        .and_then(|v| v.as_array());

    if let Some(arr) = sps {
        for sp in arr {
            let svc = sp
                .get("sv")
                .or_else(|| sp.get("service"))
                .and_then(|v| v.as_str())?;
            if svc == provider {
                return sp
                    .get("tk")
                    .or_else(|| sp.get("token"))
                    .and_then(|v| v.as_str())
                    .map(str::to_string);
            }
        }
    }

    // Legacy single-provider
    let sp = config
        .get("sp")
        .or_else(|| config.get("streaming_provider"))?;
    let svc = sp
        .get("sv")
        .or_else(|| sp.get("service"))
        .and_then(|v| v.as_str())?;
    if svc == provider {
        return sp
            .get("tk")
            .or_else(|| sp.get("token"))
            .and_then(|v| v.as_str())
            .map(str::to_string);
    }
    None
}

// ─── Import body shapes ───────────────────────────────────────────────────────
// Mirrors the Python `ImportRequest` / `TorrentOverride`.

#[derive(Deserialize, Default, Clone)]
pub struct TorrentOverride {
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub year: Option<i32>,
    #[serde(default)]
    pub r#type: Option<String>, // movie | series | sports
    #[serde(default)]
    pub sports_category: Option<String>,
}

#[derive(Deserialize)]
pub struct ImportBody {
    pub info_hashes: Vec<String>,
    #[serde(default)]
    pub overrides: Option<std::collections::HashMap<String, TorrentOverride>>,
    #[serde(default)]
    pub is_anonymous: Option<bool>,
    #[serde(default)]
    pub anonymous_display_name: Option<String>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/watchlist/{provider}/missing
pub async fn get_missing_torrents(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    Query(params): Query<MissingQuery>,
) -> Response {
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

    let config = match get_profile_config(
        &state.pool_ro,
        user_id,
        params.profile_id,
        &state.config.secret_key,
    )
    .await
    {
        Some(c) => c,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
    };

    let token = match extract_provider_token(&config, &provider) {
        Some(t) => t,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(
                    json!({"detail": format!("Provider '{}' not configured in profile", provider)}),
                ),
            )
                .into_response();
        }
    };

    // Fetch all torrents for the provider. Providers that embed files in their list
    // (TorBox, AllDebrid, Debrid-Link) populate `raw`; RD leaves it Null and we
    // fetch file details separately.
    let all_torrents =
        match crate::providers::torrents::list_downloaded_torrents(&state.http, &provider, &token)
            .await
        {
            Ok(t) => t,
            Err(e) => {
                e.log(&format!("get_missing_torrents {provider} list"));
                return (
                    e.http_status(),
                    Json(json!({"detail": e.to_string()})),
                )
                    .into_response();
            }
        };

    let all_hashes: Vec<String> = all_torrents.iter().map(|t| t.info_hash.clone()).collect();
    let existing: std::collections::HashSet<String> =
        db::filter_existing_hashes(&state.pool_ro, &all_hashes)
            .await
            .into_iter()
            .collect();

    let missing_torrents: Vec<_> = all_torrents
        .into_iter()
        .filter(|t| !existing.contains(&t.info_hash))
        .collect();

    // For RD, fetch file details per-torrent (requires separate API calls).
    // For other providers, files are already in `t.raw`.
    let file_infos: Vec<serde_json::Value> = if provider == "realdebrid" {
        let bearer =
            match crate::providers::torrents::realdebrid::resolve_bearer(&state.http, &token).await
            {
                Ok(b) => b,
                Err(e) => {
                    e.log("get_missing_torrents rd bearer");
                    return (
                        e.http_status(),
                        Json(json!({"detail": e.to_string()})),
                    )
                        .into_response();
                }
            };
        let semaphore = std::sync::Arc::new(tokio::sync::Semaphore::new(6));
        let http = state.http.clone();
        let bearer = std::sync::Arc::new(bearer);
        let futs: Vec<_> = missing_torrents
            .iter()
            .map(|t| {
                let http = http.clone();
                let bearer = bearer.clone();
                let id = t.id.clone();
                let sem = semaphore.clone();
                async move {
                    let _permit = sem.acquire().await.ok();
                    if id.is_empty() {
                        return serde_json::Value::Null;
                    }
                    crate::providers::torrents::realdebrid::get_torrent_info(&http, &bearer, &id)
                        .await
                        .unwrap_or(serde_json::Value::Null)
                }
            })
            .collect();
        futures::future::join_all(futs).await
    } else {
        // Files embedded — just clone the raw torrent objects.
        missing_torrents.iter().map(|t| t.raw.clone()).collect()
    };

    let video_extensions = [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v"];
    let sample_re = regex::Regex::new(r"(?i)(?:^|[._\-\s])sample(?:[._\-\s]|$)").unwrap();

    // Build items with parsed metadata, retaining lookup hints (db_type, search_title).
    let mut missing: Vec<serde_json::Value> = Vec::with_capacity(missing_torrents.len());
    let mut lookups: Vec<TorrentMeta> = Vec::with_capacity(missing_torrents.len());
    for (t, raw) in missing_torrents.into_iter().zip(file_infos) {
        let mut video_files = extract_video_files(&raw, &provider, &video_extensions, &sample_re);
        let meta = parse_torrent_meta(&t.name, video_files.len());

        // For racing series (F1/MotoGP), derive a per-file episode title (session
        // name, e.g. "Qualifying") so the UI can prefill episode annotations.
        if matches!(
            meta.sports_category.as_deref(),
            Some("formula_racing") | Some("motogp_racing")
        ) {
            for f in &mut video_files {
                let path = f.get("path").and_then(|v| v.as_str()).unwrap_or("");
                if let Some(title) = racing_file_episode_title(path) {
                    if let Some(obj) = f.as_object_mut() {
                        obj.insert("episode_title".to_string(), json!(title));
                    }
                }
            }
        }

        missing.push(json!({
            "info_hash": t.info_hash,
            "name": t.name,
            "size": t.size,
            "files": video_files,
            "parsed_title": meta.title,
            "parsed_year": meta.year,
            "parsed_type": meta.parsed_type,
            "sports_category": meta.sports_category,
            "matched_title": null,
            "external_ids": null,
        }));
        lookups.push(meta);
    }

    // Enrich each item with matched_title and external_ids from DB.
    for (item, meta) in missing.iter_mut().zip(&lookups) {
        let search_title = match meta.search_title.as_deref() {
            Some(t) if !t.is_empty() => t,
            _ => continue,
        };
        let (matched_title, ext_ids) =
            find_metadata_match(&state.pool_ro, search_title, meta.year, &meta.db_type).await;

        if let Some(mt) = matched_title {
            item["matched_title"] = json!(mt);
        }
        if let Some(ids) = ext_ids {
            item["external_ids"] = ids;
        }
    }

    let total = missing.len();
    Json(json!({"items": missing, "total": total, "provider": provider})).into_response()
}

/// Resolve per-torrent raw file JSON (containing `files`) for the given torrents.
/// RealDebrid needs a per-torrent info fetch; other providers embed files in `raw`.
async fn resolve_import_file_data(
    state: &AppState,
    provider: &str,
    token: &str,
    torrents: &[crate::providers::torrents::realdebrid::DownloadedTorrent],
) -> std::collections::HashMap<String, serde_json::Value> {
    use std::collections::HashMap;
    if provider == "realdebrid" {
        let bearer = match crate::providers::torrents::realdebrid::resolve_bearer(
            &state.http,
            token,
        )
        .await
        {
            Ok(b) => b,
            Err(e) => {
                tracing::warn!("resolve_import_file_data rd bearer: {e}");
                return HashMap::new();
            }
        };
        let mut map = HashMap::new();
        for t in torrents {
            if t.id.is_empty() {
                continue;
            }
            if let Ok(info) = crate::providers::torrents::realdebrid::get_torrent_info(
                &state.http,
                &bearer,
                &t.id,
            )
            .await
            {
                map.insert(t.info_hash.clone(), info);
            }
        }
        map
    } else {
        torrents
            .iter()
            .map(|t| (t.info_hash.clone(), t.raw.clone()))
            .collect()
    }
}

/// Pick a usable external meta_id (imdb → tmdb → tvdb) from a `find_metadata_match` blob.
fn meta_id_from_external_ids(ext: &serde_json::Value) -> Option<String> {
    if let Some(imdb) = ext.get("imdb").and_then(|v| v.as_str()) {
        return Some(imdb.to_string());
    }
    if let Some(tmdb) = ext.get("tmdb").and_then(|v| v.as_str()) {
        return Some(format!("tmdb:{tmdb}"));
    }
    if let Some(tvdb) = ext.get("tvdb").and_then(|v| v.as_str()) {
        return Some(format!("tvdb:{tvdb}"));
    }
    None
}

/// POST /api/v1/watchlist/{provider}/import
///
/// Quick import: analyzes each selected torrent, matches movies/series against the
/// metadata DB, creates stubs for sports, and persists with organized episodes.
pub async fn import_torrents(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    Query(params): Query<MissingQuery>,
    Json(body): Json<ImportBody>,
) -> Response {
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

    if body.info_hashes.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "No torrents provided for import"})),
        )
            .into_response();
    }

    let config = match get_profile_config(
        &state.pool_ro,
        user_id,
        params.profile_id,
        &state.config.secret_key,
    )
    .await
    {
        Some(c) => c,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
    };
    let token = match extract_provider_token(&config, &provider) {
        Some(t) => t,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(
                    json!({"detail": format!("Provider '{}' not configured in profile", provider)}),
                ),
            )
                .into_response();
        }
    };

    let torrents = match fetch_downloaded_torrents(&state, &provider, &token).await {
        Ok(t) => t,
        Err(e) => {
            e.log(&format!("import_torrents fetch {provider}"));
            return (
                e.http_status(),
                Json(json!({"detail": e.to_string()})),
            )
                .into_response();
        }
    };
    let by_hash: std::collections::HashMap<String, _> = torrents
        .iter()
        .map(|t| (t.info_hash.clone(), t.clone()))
        .collect();
    let file_data_map = resolve_import_file_data(&state, &provider, &token, &torrents).await;

    let overrides = body.overrides.unwrap_or_default();
    let video_extensions = [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v"];
    let sample_re = regex::Regex::new(r"(?i)(?:^|[._\-\s])sample(?:[._\-\s]|$)").unwrap();

    let mut imported = 0u32;
    let mut skipped = 0u32;
    let mut failed = 0u32;
    let mut details: Vec<serde_json::Value> = Vec::new();

    for raw_hash in &body.info_hashes {
        let hash = raw_hash.to_lowercase();

        // Already in DB?
        let exists: Option<i64> =
            sqlx::query_scalar("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
                .bind(&hash)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None);
        if exists.is_some() {
            skipped += 1;
            details.push(json!({"info_hash": hash, "status": "skipped", "message": "Already exists in database"}));
            continue;
        }

        let Some(torrent) = by_hash.get(&hash) else {
            failed += 1;
            details.push(json!({"info_hash": hash, "status": "failed", "message": "Torrent not found in debrid account"}));
            continue;
        };
        let torrent_name = torrent.name.clone();
        let total_size = torrent.size;

        // Extract video files for this torrent.
        let raw = file_data_map
            .get(&hash)
            .cloned()
            .unwrap_or(torrent.raw.clone());
        let video_files = extract_video_files(&raw, &provider, &video_extensions, &sample_re);
        if video_files.is_empty() {
            failed += 1;
            details.push(json!({"info_hash": hash, "status": "failed", "message": "No valid video files found (non-video/sample files are excluded)"}));
            continue;
        }
        let file_data: Vec<AdvancedImportFileEntry> = video_files
            .iter()
            .enumerate()
            .map(|(idx, f)| {
                let path = f.get("path").and_then(|v| v.as_str()).unwrap_or("");
                let filename = path.rsplit('/').next().unwrap_or(path).to_string();
                AdvancedImportFileEntry {
                    filename,
                    size: f.get("size").and_then(|v| v.as_i64()),
                    index: idx as i32,
                    season_number: None,
                    episode_number: None,
                    episode_end: None,
                    included: true,
                    episode_title: None,
                    release_date: None,
                    meta_id: None,
                    meta_title: None,
                    meta_type: None,
                }
            })
            .collect();

        // Determine metadata (apply user override, else auto-detect).
        let ov = overrides.get(raw_hash).or_else(|| overrides.get(&hash));
        let meta = parse_torrent_meta(&torrent_name, video_files.len());
        let meta_type = ov
            .and_then(|o| o.r#type.clone())
            .unwrap_or_else(|| meta.parsed_type.clone());
        let title = ov
            .and_then(|o| o.title.clone())
            .or_else(|| meta.title.clone())
            .unwrap_or_else(|| torrent_name.clone());
        let year = ov.and_then(|o| o.year).or(meta.year);

        // For movie/series, match against the metadata DB to obtain an external id.
        let mut meta_id: Option<String> = None;
        if meta_type != "sports" {
            let (_, ext_ids) = find_metadata_match(&state.pool_ro, &title, year, &meta_type).await;
            meta_id = ext_ids.as_ref().and_then(meta_id_from_external_ids);
            if meta_id.is_none() {
                failed += 1;
                details.push(json!({"info_hash": hash, "status": "failed",
                    "message": format!("No metadata match found for '{title}'")}));
                continue;
            }
        }

        let sports_category = if meta_type == "sports" {
            ov.and_then(|o| o.sports_category.clone())
        } else {
            None
        };

        let item = AdvancedImportItem {
            info_hash: hash.clone(),
            meta_type,
            meta_id,
            title: Some(title),
            sports_category,
            poster: None,
            background: None,
            logo: None,
            release_date: None,
            resolution: None,
            quality: None,
            codec: None,
            languages: None,
            catalogs: None,
            file_data: Some(file_data),
        };

        match process_advanced_import(&state, &provider, &item, &hash, &torrent_name, total_size)
            .await
        {
            Ok(Some(media_id)) => {
                imported += 1;
                details.push(json!({"info_hash": hash, "status": "success", "media_id": media_id}));
            }
            Ok(None) => {
                skipped += 1;
                details.push(json!({"info_hash": hash, "status": "skipped"}));
            }
            Err(e) => {
                failed += 1;
                tracing::warn!("import_torrents {hash}: {e}");
                details.push(json!({"info_hash": hash, "status": "failed", "message": e}));
            }
        }
    }

    Json(json!({
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "details": details,
    }))
    .into_response()
}

// ─── Advanced import body shapes ─────────────────────────────────────────────
// Mirrors the Python `AdvancedImportRequest` / `AdvancedTorrentImport` /
// `FileAnnotationData` schemas so the frontend's advanced-import payload
// deserializes correctly.

#[derive(Deserialize, Clone)]
pub struct AdvancedImportFileEntry {
    pub filename: String,
    #[serde(default)]
    pub size: Option<i64>,
    pub index: i32,
    #[serde(default)]
    pub season_number: Option<i32>,
    #[serde(default)]
    pub episode_number: Option<i32>,
    #[serde(default)]
    pub episode_end: Option<i32>,
    #[serde(default = "default_true")]
    pub included: bool,
    #[serde(default)]
    pub episode_title: Option<String>,
    #[serde(default)]
    pub release_date: Option<String>,
    #[serde(default)]
    pub meta_id: Option<String>,
    #[serde(default)]
    pub meta_title: Option<String>,
    #[serde(default)]
    pub meta_type: Option<String>,
}

fn default_true() -> bool {
    true
}

#[derive(Deserialize)]
pub struct AdvancedImportItem {
    pub info_hash: String,
    pub meta_type: String, // movie, series, sports
    #[serde(default)]
    pub meta_id: Option<String>,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub sports_category: Option<String>,
    #[serde(default)]
    pub poster: Option<String>,
    #[serde(default)]
    pub background: Option<String>,
    #[serde(default)]
    pub logo: Option<String>,
    #[serde(default)]
    pub release_date: Option<String>,
    #[serde(default)]
    pub resolution: Option<String>,
    #[serde(default)]
    pub quality: Option<String>,
    #[serde(default)]
    pub codec: Option<String>,
    #[serde(default)]
    pub languages: Option<Vec<String>>,
    #[serde(default)]
    pub catalogs: Option<Vec<String>>,
    #[serde(default)]
    pub file_data: Option<Vec<AdvancedImportFileEntry>>,
}

#[derive(Deserialize)]
pub struct AdvancedImportBody {
    pub advanced_imports: Vec<AdvancedImportItem>,
    #[serde(default)]
    pub is_anonymous: Option<bool>,
    #[serde(default)]
    pub anonymous_display_name: Option<String>,
}

/// Fetch the provider's downloaded torrents (used to resolve names/sizes on import).
async fn fetch_downloaded_torrents(
    state: &AppState,
    provider: &str,
    token: &str,
) -> Result<Vec<crate::providers::torrents::DownloadedTorrent>, crate::providers::ProviderError> {
    if !crate::providers::torrents::supports_download_list(provider) {
        return Err(crate::providers::ProviderError::api(
            format!("Unsupported provider: {provider}"),
            "api_error.mp4",
        ));
    }
    crate::providers::torrents::list_downloaded_torrents(&state.http, provider, token).await
}

// ─── Remove / clear-all body shapes ─────────────────────────────────────────

#[derive(Deserialize)]
pub struct RemoveTorrentBody {
    pub info_hash: String,
    pub profile_id: Option<i32>,
}

#[derive(Deserialize)]
pub struct ClearAllBody {
    pub profile_id: Option<i32>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/watchlist/{provider}/import/advanced
pub async fn advanced_import_torrents(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    Query(params): Query<MissingQuery>,
    Json(body): Json<AdvancedImportBody>,
) -> Response {
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

    if body.advanced_imports.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "No torrents provided for import"})),
        )
            .into_response();
    }

    // Resolve provider token from the user's profile.
    let config = match get_profile_config(
        &state.pool_ro,
        user_id,
        params.profile_id,
        &state.config.secret_key,
    )
    .await
    {
        Some(c) => c,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
    };
    let token = match extract_provider_token(&config, &provider) {
        Some(t) => t,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(
                    json!({"detail": format!("Provider '{}' not configured in profile", provider)}),
                ),
            )
                .into_response();
        }
    };

    // Fetch the provider's downloaded torrents to resolve names/sizes.
    let torrents = match fetch_downloaded_torrents(&state, &provider, &token).await {
        Ok(t) => t,
        Err(e) => {
            e.log(&format!("advanced_import_torrents fetch {provider}"));
            return (
                e.http_status(),
                Json(json!({"detail": e.to_string()})),
            )
                .into_response();
        }
    };
    let by_hash: std::collections::HashMap<String, _> = torrents
        .into_iter()
        .map(|t| (t.info_hash.clone(), t))
        .collect();

    let mut imported = 0u32;
    let mut skipped = 0u32;
    let mut failed = 0u32;
    let mut details: Vec<serde_json::Value> = Vec::new();

    for item in &body.advanced_imports {
        let hash = item.info_hash.to_lowercase();

        // movie / series imports require an external meta_id; sports may create a stub.
        if item.meta_type != "sports" && item.meta_id.as_deref().unwrap_or("").is_empty() {
            failed += 1;
            details.push(json!({"info_hash": hash, "status": "failed",
                "message": "meta_id is required for movie and series imports"}));
            continue;
        }

        // Skip if already in the DB.
        let exists: Option<i64> =
            sqlx::query_scalar("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
                .bind(&hash)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None);
        if exists.is_some() {
            skipped += 1;
            details.push(json!({"info_hash": hash, "status": "skipped",
                "message": "Already exists in database"}));
            continue;
        }

        let torrent = by_hash.get(&hash);
        let torrent_name = torrent
            .map(|t| t.name.clone())
            .or_else(|| item.title.clone())
            .unwrap_or_else(|| hash.clone());
        let total_size = torrent.map(|t| t.size).unwrap_or(0);

        match process_advanced_import(&state, &provider, item, &hash, &torrent_name, total_size)
            .await
        {
            Ok(Some(media_id)) => {
                imported += 1;
                details.push(json!({"info_hash": hash, "status": "success", "media_id": media_id}));
            }
            Ok(None) => {
                skipped += 1;
                details.push(json!({"info_hash": hash, "status": "skipped"}));
            }
            Err(e) => {
                failed += 1;
                tracing::warn!("advanced_import_torrents {hash}: {e}");
                details.push(json!({"info_hash": hash, "status": "failed", "message": e}));
            }
        }
    }

    Json(json!({
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "details": details,
    }))
    .into_response()
}

/// Persist a single advanced import: resolve/create media, insert the stream,
/// link files (with season/episode), languages, and catalogs.
/// Returns `Ok(Some(media_id))` on success, `Ok(None)` if it already existed.
async fn process_advanced_import(
    state: &AppState,
    provider: &str,
    item: &AdvancedImportItem,
    hash: &str,
    torrent_name: &str,
    total_size: i64,
) -> Result<Option<i64>, String> {
    let title = item.title.as_deref().unwrap_or(torrent_name);

    // Parse the torrent name for technical metadata; UI overrides take precedence.
    let mut parsed = if crate::parser::is_sports_title(torrent_name) {
        crate::parser::parse_sports_title(torrent_name)
    } else {
        crate::parser::parse_title(torrent_name)
    };
    if let Some(r) = item.resolution.clone() {
        parsed.resolution = Some(r);
    }
    if let Some(q) = item.quality.clone() {
        parsed.quality = Some(q);
    }
    if let Some(c) = item.codec.clone() {
        parsed.codec = Some(c);
    }

    // Resolve the sports category and catalogs.
    let sports_category: Option<String> = if item.meta_type == "sports" {
        item.sports_category
            .clone()
            .or_else(|| crate::parser::detect_sports_category(torrent_name).map(str::to_string))
            .or(Some("other_sports".to_string()))
    } else {
        None
    };

    // ── Resolve / create the primary media ──────────────────────────────────
    let media_id: i64 = if item.meta_type == "sports" {
        let cat = sports_category.as_deref().unwrap_or("other_sports");
        // F1/MotoGP are stored as series; everything else as a movie.
        let db_type = if matches!(cat, "formula_racing" | "motogp_racing") {
            "SERIES"
        } else {
            "MOVIE"
        };
        crate::scrapers::media_resolve::find_or_create_sports_stub(
            &state.pool,
            title,
            parsed.year,
            item.poster.as_deref(),
            db_type,
        )
        .await
        .ok_or_else(|| "Failed to create sports media".to_string())? as i64
    } else {
        let meta_id = item.meta_id.as_deref().unwrap_or("");
        super::content::import_helpers::resolve_media_for_import(
            &state.pool,
            &state.http,
            state.config.tmdb_api_key.as_deref(),
            state.config.tvdb_api_key.as_deref(),
            meta_id,
            &item.meta_type,
            crate::scrapers::media_resolve::ImportMediaOverrides {
                title: item.title.as_deref(),
                poster: item.poster.as_deref(),
                background: item.background.as_deref(),
                release_date: item.release_date.as_deref(),
                year: parsed.year,
            },
            None,
        )
        .await
        .ok_or_else(|| format!("Could not resolve media for {meta_id}"))? as i64
    };

    // ── Build the file rows (annotations → video files only) ────────────────
    let sample_re = regex::Regex::new(r"(?i)(?:^|[._\-\s])sample(?:[._\-\s]|$)").unwrap();
    let video_extensions = [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v"];
    let mut file_rows: Vec<serde_json::Value> = Vec::new();
    if let Some(files) = &item.file_data {
        for f in files {
            if !f.included {
                continue;
            }
            if !is_wanted_video(&f.filename, &video_extensions, &sample_re) {
                continue;
            }
            file_rows.push(json!({
                "index": f.index,
                "filename": f.filename,
                "size": f.size.unwrap_or(0),
                "season_number": f.season_number,
                "episode_number": f.episode_number,
                "episode_end": f.episode_end,
                "episode_title": f.episode_title,
                "release_date": f.release_date,
                "meta_id": f.meta_id,
                "meta_type": f.meta_type,
                "meta_title": f.meta_title,
                "sports_category": sports_category,
            }));
        }
    }
    if file_rows.is_empty() {
        return Err("No valid video files found (non-video/sample files are excluded)".to_string());
    }

    // ── Catalogs (sports category prepended for sports) ─────────────────────
    let mut catalogs = item.catalogs.clone().unwrap_or_default();
    if let Some(cat) = &sports_category {
        if !catalogs.iter().any(|c| c == cat) {
            catalogs.insert(0, cat.clone());
        }
    }

    let languages = item
        .languages
        .clone()
        .unwrap_or_else(|| parsed.languages.clone());
    let file_count = file_rows.len() as i32;
    let source = item.meta_id.as_deref().unwrap_or(provider).to_string();
    let prefetch = crate::scrapers::media_resolve::ImportMetadataCache::default();

    // ── Persist everything via the shared import routine ─────────────────────
    super::content::import_helpers::persist_torrent_import(
        &state.pool,
        &state.http,
        super::content::import_helpers::TorrentImportPersist {
            info_hash: hash,
            name: torrent_name,
            source: &source,
            total_size: if total_size > 0 {
                Some(total_size)
            } else {
                None
            },
            seeders: None,
            file_count,
            parsed: &parsed,
            media_id: Some(media_id),
            meta_type: &item.meta_type,
            is_public: true,
            file_rows: &file_rows,
            languages: &languages,
            catalogs: &catalogs,
            trackers: &[],
            sports_category: sports_category.as_deref(),
            fallback_title: title,
            tmdb_api_key: state.config.tmdb_api_key.as_deref(),
            tvdb_api_key: state.config.tvdb_api_key.as_deref(),
            prefetch: &prefetch,
            torrent_type: crate::db::TorrentType::Public,
            torrent_file: None,
            uploader: None,
            uploader_user_id: None,
        },
    )
    .await
    .map_err(|e| e.to_string())?;

    Ok(Some(media_id))
}

/// POST /api/v1/watchlist/{provider}/remove
pub async fn remove_torrent_from_debrid(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    Json(body): Json<RemoveTorrentBody>,
) -> Response {
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

    let config = match get_profile_config(
        &state.pool_ro,
        user_id,
        body.profile_id,
        &state.config.secret_key,
    )
    .await
    {
        Some(c) => c,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
    };

    let token = match extract_provider_token(&config, &provider) {
        Some(t) => t,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(
                    json!({"detail": format!("Provider '{}' not configured in profile", provider)}),
                ),
            )
                .into_response();
        }
    };

    let info_hash = body.info_hash.to_lowercase();

    use crate::providers::torrents;

    let result = match provider.as_str() {
        "realdebrid" => {
            torrents::realdebrid::delete_torrent_by_hash(&state.http, &token, &info_hash).await
        }
        "alldebrid" => {
            torrents::alldebrid::delete_torrent_by_hash(&state.http, &token, &info_hash).await
        }
        "debridlink" => {
            torrents::debridlink::delete_torrent_by_hash(&state.http, &token, &info_hash).await
        }
        "torbox" => torrents::torbox::delete_torrent_by_hash(&state.http, &token, &info_hash).await,
        "offcloud" => {
            torrents::offcloud::delete_torrent_by_hash(&state.http, &token, &info_hash).await
        }
        "premiumize" => {
            torrents::premiumize::delete_torrent_by_hash(&state.http, &token, &info_hash).await
        }
        "seedr" => torrents::seedr::delete_torrent_by_hash(&state.http, &token, &info_hash).await,
        "pikpak" => torrents::pikpak::delete_torrent_by_hash(&state.http, &token, &info_hash).await,
        other => {
            return (
                StatusCode::NOT_IMPLEMENTED,
                Json(json!({"success": false, "detail": format!("Single-torrent removal not supported for '{other}'")})),
            )
                .into_response();
        }
    };

    match result {
        Ok(true) => Json(json!({"success": true, "info_hash": info_hash})).into_response(),
        Ok(false) => (
            StatusCode::NOT_FOUND,
            Json(json!({"success": false, "detail": "Torrent not found in provider account"})),
        )
            .into_response(),
        Err(e) => {
            e.log(&format!("remove_torrent_from_debrid {provider}"));
            (
                e.http_status(),
                Json(json!({"success": false, "detail": e.to_string()})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/watchlist/{provider}/clear-all
pub async fn clear_all_torrents_from_debrid(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    Json(body): Json<ClearAllBody>,
) -> Response {
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

    let config = match get_profile_config(
        &state.pool_ro,
        user_id,
        body.profile_id,
        &state.config.secret_key,
    )
    .await
    {
        Some(c) => c,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
    };

    let token = match extract_provider_token(&config, &provider) {
        Some(t) => t,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(
                    json!({"detail": format!("Provider '{}' not configured in profile", provider)}),
                ),
            )
                .into_response();
        }
    };

    use crate::providers::torrents;

    let result = match provider.as_str() {
        "realdebrid" => torrents::realdebrid::delete_all_torrents(&state.http, &token).await,
        "alldebrid" => torrents::alldebrid::delete_all_torrents(&state.http, &token).await,
        "premiumize" => torrents::premiumize::delete_all_torrents(&state.http, &token).await,
        "debridlink" => torrents::debridlink::delete_all_torrents(&state.http, &token).await,
        "torbox" => torrents::torbox::delete_all_torrents(&state.http, &token).await,
        "stremthru" => torrents::stremthru::delete_all_torrents(&state.http, &token).await,
        "offcloud" => torrents::offcloud::delete_all_torrents(&state.http, &token).await,
        "easydebrid" => torrents::easydebrid::delete_all_torrents(&state.http, &token).await,
        "seedr" => torrents::seedr::delete_all_torrents(&state.http, &token).await,
        "pikpak" => torrents::pikpak::delete_all_torrents(&state.http, &token).await,
        other => {
            return (
                StatusCode::NOT_IMPLEMENTED,
                Json(json!({"success": false, "detail": format!("Clear-all not supported for '{other}'")})),
            )
                .into_response();
        }
    };

    match result {
        Ok(()) => Json(json!({"success": true, "provider": provider})).into_response(),
        Err(e) => {
            e.log(&format!("clear_all_torrents_from_debrid {provider}"));
            (
                e.http_status(),
                Json(json!({"success": false, "detail": e.to_string()})),
            )
                .into_response()
        }
    }
}

// ─── Aliases for mod.rs compatibility ────────────────────────────────────────

pub use check_in_library as get_library_status;
pub use get_library as list_library;

pub async fn bulk_library_operation(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> impl IntoResponse {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    let operation = match body.get("operation").and_then(|v| v.as_str()) {
        Some(op) => op.to_string(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Missing 'operation' field"})),
            )
                .into_response()
        }
    };

    let items = match body.get("items").and_then(|v| v.as_array()) {
        Some(arr) => arr.clone(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Missing or invalid 'items' field"})),
            )
                .into_response()
        }
    };

    let mut count: usize = 0;
    match operation.as_str() {
        "add" => {
            for item in &items {
                let media_id = match item.get("media_id").and_then(|v| v.as_i64()) {
                    Some(id) => id,
                    None => continue,
                };
                let result = sqlx::query(
                    "INSERT INTO user_library_item (user_id, media_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                )
                .bind(user_id as i32)
                .bind(media_id as i32)
                .execute(&state.pool)
                .await;
                if result.is_ok() {
                    count += 1;
                }
            }
            Json(serde_json::json!({
                "status": "success",
                "operation": "add",
                "count": count,
                "message": format!("{count} item(s) added to library"),
            }))
            .into_response()
        }
        "remove" => {
            for item in &items {
                let media_id = match item.get("media_id").and_then(|v| v.as_i64()) {
                    Some(id) => id,
                    None => continue,
                };
                let result = sqlx::query(
                    "DELETE FROM user_library_item WHERE user_id = $1 AND media_id = $2",
                )
                .bind(user_id as i32)
                .bind(media_id as i32)
                .execute(&state.pool)
                .await;
                if let Ok(r) = result {
                    count += r.rows_affected() as usize;
                }
            }
            Json(serde_json::json!({
                "status": "success",
                "operation": "remove",
                "count": count,
                "message": format!("{count} item(s) removed from library"),
            }))
            .into_response()
        }
        _ => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Invalid operation. Must be 'add' or 'remove'"})),
        )
            .into_response(),
    }
}
