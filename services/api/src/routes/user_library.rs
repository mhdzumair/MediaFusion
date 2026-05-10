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

use serde_json::json;

use crate::{db, state::AppState};

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
    .bind(user_id as i32)
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
        .bind(user_id as i32)
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

    let ext = get_external_ids(&state.pool, body.media_id as i32).await;
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

    let result = sqlx::query("DELETE FROM user_library_item WHERE media_id = $1 AND user_id = $2")
        .bind(media_id)
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
        deep_merge_cfg(&mut full_config, secrets);
    }
    Some(full_config)
}

fn deep_merge_cfg(base: &mut serde_json::Value, overlay: serde_json::Value) {
    match (base, overlay) {
        (serde_json::Value::Object(b), serde_json::Value::Object(o)) => {
            for (k, v) in o {
                deep_merge_cfg(b.entry(k).or_insert(serde_json::Value::Null), v);
            }
        }
        (base, overlay) => *base = overlay,
    }
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

// ─── Debrid import DB helper ──────────────────────────────────────────────────

/// Insert a single torrent stream row; returns true if newly inserted, false if already existed.
async fn upsert_debrid_torrent(
    pool: &sqlx::PgPool,
    info_hash: &str,
    name: &str,
    source: &str,
    size: i64,
) -> Result<bool, sqlx::Error> {
    // Check for existing entry first to avoid unnecessary inserts
    let exists: Option<i64> =
        sqlx::query_scalar("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
            .bind(info_hash)
            .fetch_optional(pool)
            .await?;
    if exists.is_some() {
        return Ok(false);
    }

    let parsed = crate::parser::parse_title(name);
    let mut txn = pool.begin().await?;

    let stream_id: i64 = sqlx::query_scalar(
        r#"INSERT INTO stream(
               stream_type, name, source, resolution, codec, quality,
               is_proper, is_repack, is_extended, is_complete, is_dubbed, release_group,
               is_active, is_blocked, is_public, playback_count, created_at
           ) VALUES(
               'TORRENT'::streamtype, $1, $2, $3, $4, $5,
               $6, $7, $8, $9, $10, $11,
               true, false, true, 0, NOW()
           ) RETURNING id"#,
    )
    .bind(name)
    .bind(source)
    .bind(parsed.resolution.as_deref())
    .bind(parsed.codec.as_deref())
    .bind(parsed.quality.as_deref())
    .bind(parsed.is_proper)
    .bind(parsed.is_repack)
    .bind(parsed.is_extended)
    .bind(parsed.is_complete)
    .bind(parsed.is_dubbed)
    .bind(parsed.release_group.as_deref())
    .fetch_one(&mut *txn)
    .await?;

    let inserted = sqlx::query(
        r#"INSERT INTO torrent_stream(stream_id, info_hash, total_size, seeders, torrent_type, file_count, created_at)
           VALUES($1, $2, $3, NULL, 'PUBLIC'::torrenttype, 0, NOW())
           ON CONFLICT (info_hash) DO NOTHING"#,
    )
    .bind(stream_id as i32)
    .bind(info_hash)
    .bind(size)
    .execute(&mut *txn)
    .await?
    .rows_affected()
        > 0;

    if !inserted {
        sqlx::query("DELETE FROM stream WHERE id = $1")
            .bind(stream_id as i32)
            .execute(&mut *txn)
            .await
            .ok();
    }

    txn.commit().await?;
    Ok(inserted)
}

// ─── Import body shapes ───────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ImportBody {
    items: Vec<ImportItem>,
}

#[derive(Deserialize)]
pub struct ImportItem {
    info_hash: String,
    name: String,
    #[serde(default)]
    size: i64,
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

    // Only Real-Debrid is implemented; others fall back to empty list
    let all_torrents = match provider.as_str() {
        "realdebrid" => {
            match crate::providers::torrents::realdebrid::list_downloaded_torrents(
                &state.http,
                &token,
            )
            .await
            {
                Ok(t) => t,
                Err(e) => {
                    tracing::warn!("get_missing_torrents realdebrid: {e}");
                    return (
                        StatusCode::BAD_GATEWAY,
                        Json(json!({"detail": format!("Provider error: {e}")})),
                    )
                        .into_response();
                }
            }
        }
        _ => {
            return Json(json!({"items": [], "total": 0, "provider": provider})).into_response();
        }
    };

    let all_hashes: Vec<String> = all_torrents.iter().map(|t| t.info_hash.clone()).collect();
    let existing: std::collections::HashSet<String> =
        db::filter_existing_hashes(&state.pool_ro, &all_hashes)
            .await
            .into_iter()
            .collect();

    let missing: Vec<serde_json::Value> = all_torrents
        .into_iter()
        .filter(|t| !existing.contains(&t.info_hash))
        .map(|t| {
            json!({
                "info_hash": t.info_hash,
                "name": t.name,
                "size": t.size,
            })
        })
        .collect();

    let total = missing.len();
    Json(json!({"items": missing, "total": total, "provider": provider})).into_response()
}

/// POST /api/v1/watchlist/{provider}/import
pub async fn import_torrents(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(provider): Path<String>,
    Json(body): Json<ImportBody>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let mut imported = 0u32;
    let mut skipped = 0u32;
    let mut failed = 0u32;
    let mut details: Vec<serde_json::Value> = Vec::new();

    for item in body.items {
        let hash = item.info_hash.to_lowercase();
        match upsert_debrid_torrent(&state.pool, &hash, &item.name, &provider, item.size).await {
            Ok(true) => {
                imported += 1;
                details.push(json!({"info_hash": hash, "status": "imported"}));
            }
            Ok(false) => {
                skipped += 1;
                details.push(json!({"info_hash": hash, "status": "skipped"}));
            }
            Err(e) => {
                failed += 1;
                tracing::warn!("import_torrents upsert {hash}: {e}");
                details
                    .push(json!({"info_hash": hash, "status": "failed", "error": e.to_string()}));
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

#[derive(Deserialize)]
pub struct AdvancedImportFileEntry {
    pub index: i32,
    pub filename: String,
    pub size: i64,
    pub season_number: Option<i32>,
    pub episode_number: Option<i32>,
}

#[derive(Deserialize)]
pub struct AdvancedImportItem {
    pub info_hash: String,
    pub name: String,
    #[serde(default)]
    pub size: i64,
    pub languages: Option<Vec<String>>,
    pub file_data: Option<Vec<AdvancedImportFileEntry>>,
}

#[derive(Deserialize)]
pub struct AdvancedImportBody {
    pub items: Vec<AdvancedImportItem>,
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
    Json(body): Json<AdvancedImportBody>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let mut imported = 0u32;
    let mut skipped = 0u32;
    let mut failed = 0u32;
    let mut details: Vec<serde_json::Value> = Vec::new();

    for item in body.items {
        let hash = item.info_hash.to_lowercase();

        match upsert_debrid_torrent(&state.pool, &hash, &item.name, &provider, item.size).await {
            Ok(newly_inserted) => {
                if newly_inserted {
                    // Insert languages
                    if let Some(langs) = &item.languages {
                        for lang in langs {
                            if lang.is_empty() {
                                continue;
                            }
                            let lid: Option<i32> = sqlx::query_scalar(
                                "INSERT INTO language(name) VALUES($1) \
                                 ON CONFLICT(name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                            )
                            .bind(lang)
                            .fetch_optional(&state.pool)
                            .await
                            .unwrap_or(None);
                            if let Some(lid) = lid {
                                sqlx::query(
                                    "INSERT INTO stream_language_link(stream_id, language_id, language_type) \
                                     VALUES((SELECT stream_id FROM torrent_stream WHERE info_hash = $1 LIMIT 1), $2, 'audio') \
                                     ON CONFLICT DO NOTHING",
                                )
                                .bind(&hash)
                                .bind(lid)
                                .execute(&state.pool)
                                .await
                                .ok();
                            }
                        }
                    }

                    // Insert per-file metadata
                    if let Some(files) = &item.file_data {
                        // Look up stream_id for this hash
                        let stream_id: Option<i64> = sqlx::query_scalar(
                            "SELECT stream_id FROM torrent_stream WHERE info_hash = $1 LIMIT 1",
                        )
                        .bind(&hash)
                        .fetch_optional(&state.pool)
                        .await
                        .unwrap_or(None);

                        if let Some(sid) = stream_id {
                            for f in files {
                                let fid: Option<i32> = sqlx::query_scalar(
                                    r#"INSERT INTO stream_file(stream_id, file_index, filename, size, file_type)
                                       VALUES($1, $2, $3, $4, 'video')
                                       ON CONFLICT DO NOTHING RETURNING id"#,
                                )
                                .bind(sid as i32)
                                .bind(f.index)
                                .bind(&f.filename)
                                .bind(f.size)
                                .fetch_optional(&state.pool)
                                .await
                                .unwrap_or(None);

                                if let (Some(fid), Some(s), Some(e)) =
                                    (fid, f.season_number, f.episode_number)
                                {
                                    sqlx::query(
                                        r#"INSERT INTO file_media_link(file_id, media_id, season_number, episode_number)
                                           SELECT $1,
                                                  (SELECT media_id FROM stream_media_link WHERE stream_id = $2 LIMIT 1),
                                                  $3, $4
                                           WHERE (SELECT media_id FROM stream_media_link WHERE stream_id = $2 LIMIT 1) IS NOT NULL
                                           ON CONFLICT DO NOTHING"#,
                                    )
                                    .bind(fid)
                                    .bind(sid as i32)
                                    .bind(s)
                                    .bind(e)
                                    .execute(&state.pool)
                                    .await
                                    .ok();
                                }
                            }
                        }
                    }

                    imported += 1;
                    details.push(json!({"info_hash": hash, "status": "imported"}));
                } else {
                    skipped += 1;
                    details.push(json!({"info_hash": hash, "status": "skipped"}));
                }
            }
            Err(e) => {
                failed += 1;
                tracing::warn!("advanced_import_torrents upsert {hash}: {e}");
                details
                    .push(json!({"info_hash": hash, "status": "failed", "error": e.to_string()}));
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
            tracing::warn!("remove_torrent_from_debrid {provider}: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"success": false, "detail": format!("Provider error: {e}")})),
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
            tracing::warn!("clear_all_torrents_from_debrid {provider}: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"success": false, "detail": format!("Provider error: {e}")})),
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
