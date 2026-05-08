/// Admin endpoints for cache and database management.
///
/// All endpoints require a valid JWT token with role == "admin".
///
/// Routes:
///   GET    /api/v1/admin/cache/stats
///   GET    /api/v1/admin/cache/keys
///   DELETE /api/v1/admin/cache/key/{*key}
///   POST   /api/v1/admin/cache/clear
///   GET    /api/v1/admin/db/stats
///   GET    /api/v1/admin/db/tables
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use fred::prelude::*;
use fred::types::InfoKind;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helper ─────────────────────────────────────────────────────────────

fn validate_admin(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
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

fn forbidden() -> impl IntoResponse {
    (
        StatusCode::FORBIDDEN,
        Json(serde_json::json!({"error": "Forbidden"})),
    )
}

// ─── Cache patterns map ───────────────────────────────────────────────────────

fn cache_type_to_pattern(cache_type: &str) -> Option<&'static str> {
    match cache_type {
        "scrapers" => Some("rss_scraper:*"),
        "metadata" => Some("meta_cache:*"),
        "catalog" => Some("catalog:*"),
        "streams" => Some("torrent_streams:*"),
        "debrid" => Some("debrid_cache:*"),
        "profiles" => Some("profile_enc:*"),
        "events" => Some("events:*"),
        "genres" => Some("genres:*"),
        "images" => Some("poster_src:*"),
        "rate_limit" => Some("rate_limit:*"),
        _ => None,
    }
}

// ─── SCAN helper ─────────────────────────────────────────────────────────────

/// Execute a single SCAN page, returning (next_cursor, keys).
/// Returns (0, []) on error.
async fn redis_scan_page(
    redis: &fred::clients::Client,
    cursor: &str,
    pattern: &str,
    count: u32,
) -> (String, Vec<String>) {
    let result: Result<Value, _> = redis
        .scan_page(cursor.to_string(), pattern.to_string(), Some(count), None)
        .await;

    match result {
        Ok(value) => parse_scan_value(value),
        Err(e) => {
            tracing::error!("Redis SCAN error: {e}");
            ("0".to_string(), Vec::new())
        }
    }
}

/// Parse the SCAN response Value into (next_cursor, keys).
fn parse_scan_value(value: Value) -> (String, Vec<String>) {
    // Redis SCAN returns: [cursor_bulk_string, [key1, key2, ...]]
    if let Value::Array(arr) = value {
        if arr.len() == 2 {
            let cursor = match &arr[0] {
                Value::String(s) => s.to_string(),
                Value::Bytes(b) => String::from_utf8_lossy(b).to_string(),
                Value::Integer(n) => n.to_string(),
                other => format!("{other:?}"),
            };
            let keys = if let Value::Array(key_arr) = &arr[1] {
                key_arr
                    .iter()
                    .filter_map(|v| match v {
                        Value::String(s) => Some(s.to_string()),
                        Value::Bytes(b) => Some(String::from_utf8_lossy(b).to_string()),
                        _ => None,
                    })
                    .collect()
            } else {
                Vec::new()
            };
            return (cursor, keys);
        }
    }
    ("0".to_string(), Vec::new())
}

// ─── Response types ───────────────────────────────────────────────────────────

#[derive(Serialize)]
pub struct CacheStatsResponse {
    pub used_memory: String,
    pub connected_clients: String,
    pub total_commands: String,
    pub hit_ratio: f64,
    pub total_keys: i64,
}

#[derive(Serialize)]
pub struct CacheKeysResponse {
    pub keys: Vec<String>,
    pub cursor: String,
    pub has_more: bool,
}

#[derive(Serialize)]
pub struct CacheDeleteResponse {
    pub success: bool,
    pub deleted: i64,
}

#[derive(Serialize)]
pub struct CacheClearResponse {
    pub cleared: i64,
    pub pattern: String,
}

#[derive(Serialize)]
pub struct DbStatsResponse {
    pub version: String,
    pub database: String,
    pub size: String,
    pub active_connections: i64,
    pub torrent_streams: i64,
    pub movies: i64,
    pub series: i64,
    pub usenet_streams: i64,
    pub telegram_streams: i64,
    pub users: i64,
}

#[derive(Serialize)]
pub struct TableInfo {
    pub schema: String,
    pub table: String,
    pub size: String,
    pub row_estimate: i64,
}

// ─── Query param types ────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct CacheKeysParams {
    #[serde(default = "default_pattern")]
    pub pattern: String,
    #[serde(default = "default_count")]
    pub count: i64,
    #[serde(default = "default_cursor")]
    pub cursor: String,
}

fn default_pattern() -> String {
    "*".to_string()
}

fn default_count() -> i64 {
    50
}

fn default_cursor() -> String {
    "0".to_string()
}

#[derive(Deserialize)]
pub struct CacheClearRequest {
    pub pattern: Option<String>,
    pub cache_type: Option<String>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

pub async fn cache_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden().into_response();
    }

    // Fetch Redis INFO
    let info_result: Result<String, _> = state.redis.info(Some(InfoKind::All)).await;
    let info_str = match info_result {
        Ok(s) => s,
        Err(e) => {
            tracing::error!("Redis INFO error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Redis error"})),
            )
                .into_response();
        }
    };

    // Parse key:value pairs from INFO output
    let mut used_memory = "unknown".to_string();
    let mut connected_clients = "0".to_string();
    let mut total_commands = "0".to_string();
    let mut keyspace_hits: f64 = 0.0;
    let mut keyspace_misses: f64 = 0.0;

    for line in info_str.lines() {
        if let Some((key, value)) = line.split_once(':') {
            let key = key.trim();
            let value = value.trim();
            match key {
                "used_memory_human" => used_memory = value.to_string(),
                "connected_clients" => connected_clients = value.to_string(),
                "total_commands_processed" => total_commands = value.to_string(),
                "keyspace_hits" => keyspace_hits = value.parse().unwrap_or(0.0),
                "keyspace_misses" => keyspace_misses = value.parse().unwrap_or(0.0),
                _ => {}
            }
        }
    }

    // DBSIZE
    let total_keys: i64 = state.redis.dbsize().await.unwrap_or(0);

    let hit_ratio = if keyspace_hits + keyspace_misses > 0.0 {
        keyspace_hits / (keyspace_hits + keyspace_misses)
    } else {
        0.0
    };

    Json(CacheStatsResponse {
        used_memory,
        connected_clients,
        total_commands,
        hit_ratio,
        total_keys,
    })
    .into_response()
}

pub async fn cache_keys(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<CacheKeysParams>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden().into_response();
    }

    let count = params.count.clamp(1, 200) as u32;
    let (next_cursor, keys) =
        redis_scan_page(&state.redis, &params.cursor, &params.pattern, count).await;

    let has_more = next_cursor != "0";
    Json(CacheKeysResponse {
        keys,
        cursor: next_cursor,
        has_more,
    })
    .into_response()
}

pub async fn cache_key_delete(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(key): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden().into_response();
    }

    let deleted: i64 = state.redis.del(&key).await.unwrap_or(0);
    Json(CacheDeleteResponse {
        success: true,
        deleted,
    })
    .into_response()
}

pub async fn cache_clear(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<CacheClearRequest>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden().into_response();
    }

    // Resolve pattern
    let pattern = if let Some(p) = body.pattern.filter(|s| !s.is_empty()) {
        p
    } else if let Some(ct) = body.cache_type.as_deref() {
        match cache_type_to_pattern(ct) {
            Some(p) => p.to_string(),
            None => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({"error": format!("Unknown cache_type: {ct}")})),
                )
                    .into_response();
            }
        }
    } else {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": "pattern or cache_type required"})),
        )
            .into_response();
    };

    // Scan and delete in chunks of 100
    let mut total_cleared: i64 = 0;
    let mut cursor = "0".to_string();

    loop {
        let (next_cursor, keys) = redis_scan_page(&state.redis, &cursor, &pattern, 100).await;

        if !keys.is_empty() {
            let deleted: i64 = state.redis.del(keys).await.unwrap_or(0);
            total_cleared += deleted;
        }
        cursor = next_cursor;
        if cursor == "0" {
            break;
        }
    }

    Json(CacheClearResponse {
        cleared: total_cleared,
        pattern,
    })
    .into_response()
}

pub async fn db_stats(headers: HeaderMap, State(state): State<Arc<AppState>>) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden().into_response();
    }

    let (version, database, size, active_conn, torrents, movies, series, usenets, telegrams, users) = tokio::join!(
        fetch_scalar_str(&state.pool_ro, "SELECT version()"),
        fetch_scalar_str(&state.pool_ro, "SELECT current_database()"),
        fetch_scalar_str(
            &state.pool_ro,
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        ),
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'"
        ),
        fetch_scalar_i64(&state.pool_ro, "SELECT COUNT(*) FROM torrent_stream"),
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT COUNT(*) FROM media WHERE media_type = 'movie'"
        ),
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT COUNT(*) FROM media WHERE media_type = 'series'"
        ),
        fetch_scalar_i64(&state.pool_ro, "SELECT COUNT(*) FROM usenet_stream"),
        fetch_scalar_i64(&state.pool_ro, "SELECT COUNT(*) FROM telegram_stream"),
        fetch_scalar_i64(&state.pool_ro, "SELECT COUNT(*) FROM users"),
    );

    Json(DbStatsResponse {
        version,
        database,
        size,
        active_connections: active_conn,
        torrent_streams: torrents,
        movies,
        series,
        usenet_streams: usenets,
        telegram_streams: telegrams,
        users,
    })
    .into_response()
}

pub async fn db_tables(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden().into_response();
    }

    let rows = sqlx::query_as::<_, (String, String, String, i64)>(
        r#"
        SELECT
            schemaname,
            relname as tablename,
            pg_size_pretty(pg_total_relation_size(schemaname||'.'||relname)) as size,
            n_live_tup as row_estimate
        FROM pg_stat_user_tables
        ORDER BY pg_total_relation_size(schemaname||'.'||relname) DESC
        LIMIT 50
        "#,
    )
    .fetch_all(&state.pool_ro)
    .await;

    match rows {
        Ok(rows) => {
            let tables: Vec<TableInfo> = rows
                .into_iter()
                .map(|(schema, table, size, row_estimate)| TableInfo {
                    schema,
                    table,
                    size,
                    row_estimate,
                })
                .collect();
            Json(tables).into_response()
        }
        Err(e) => {
            tracing::error!("DB tables query error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Database error"})),
            )
                .into_response()
        }
    }
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

async fn fetch_scalar_str(pool: &sqlx::PgPool, query: &str) -> String {
    sqlx::query_scalar::<_, String>(query)
        .fetch_one(pool)
        .await
        .unwrap_or_else(|_| "unknown".to_string())
}

async fn fetch_scalar_i64(pool: &sqlx::PgPool, query: &str) -> i64 {
    sqlx::query_scalar::<_, i64>(query)
        .fetch_one(pool)
        .await
        .unwrap_or(0)
}
