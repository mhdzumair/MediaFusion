/// Admin endpoints for cache and database management.
///
/// All endpoints require a valid JWT token with role == "admin".
///
/// Routes:
///   GET    /api/v1/admin/cache/stats
///   GET    /api/v1/admin/cache/keys
///   DELETE /api/v1/admin/cache/key/{key}
///   DELETE /api/v1/admin/cache/key/{key}/item
///   POST   /api/v1/admin/cache/clear
///   GET    /api/v1/admin/db/stats
///   GET    /api/v1/admin/db/tables
use std::sync::Arc;

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
};
use fred::prelude::*;
use fred::types::InfoKind;
use serde::{Deserialize, Serialize};
use serde_json::{Value as JsonValue, json};

use crate::state::AppState;

use super::auth_guard;

// ─── Cache patterns map ───────────────────────────────────────────────────────

/// Return all Redis glob patterns for a named cache category.
/// Driven by CACHE_PATTERNS so stats and clear always agree.
fn cache_type_to_patterns(cache_type: &str) -> Option<&'static [&'static str]> {
    CACHE_PATTERNS
        .iter()
        .find(|(name, _, _)| *name == cache_type)
        .map(|(_, _, patterns)| *patterns)
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

// CacheStatsResponse, CacheKeysResponse defined inline as json! below

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
    pub database_name: String,
    pub size_human: String,
    pub total_size_bytes: i64,
    pub connection_count: i64,
    pub max_connections: i64,
    pub cache_hit_ratio: f64,
    pub uptime_seconds: i64,
    pub active_queries: i64,
    pub deadlocks: i64,
    pub transactions_committed: i64,
    pub transactions_rolled_back: i64,
}

#[derive(Serialize)]
pub struct TableInfo {
    pub name: String,
    pub schema_name: String,
    pub row_count: i64,
    pub size_human: String,
    pub size_bytes: i64,
    pub index_size_human: String,
    pub index_size_bytes: i64,
    pub last_vacuum: Option<String>,
    pub last_analyze: Option<String>,
    pub last_autovacuum: Option<String>,
    pub last_autoanalyze: Option<String>,
}

#[derive(Serialize)]
pub struct TablesListResponse {
    pub tables: Vec<TableInfo>,
    pub total_count: usize,
    pub total_size_human: String,
    pub total_size_bytes: i64,
}

// ─── Query param types ────────────────────────────────────────────────────────

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
    // frontend sends `type` — accept as alias
    #[serde(rename = "type")]
    pub type_field: Option<String>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

// Cache type name → list of glob patterns to count
const CACHE_PATTERNS: &[(&str, &str, &[&str])] = &[
    (
        "scrapers",
        "Scraper cooldown sorted sets",
        &[
            "rss_scraper",
            "zilean",
            "yts",
            "torrentio",
            "prowlarr",
            "mediafusion",
            "jackett",
            "bt4g",
            "telegram_scraper",
            "public_indexers",
            "easynews",
            "torbox_search",
            "newznab",
            "torznab:*",
            "newznab:*",
            "jackett:*",
            "prowlarr:*",
        ],
    ),
    (
        "metadata",
        "Movie/TV show metadata",
        &[
            "movie_exists:*",
            "series_exists:*",
            "tv_exists:*",
            "meta_cache:*",
        ],
    ),
    ("catalog", "Catalog browse cache", &["catalog:*", "mf:*"]),
    (
        "streams",
        "Stream data cache",
        &["torrent_streams:*", "stream:*", "stream_data:*"],
    ),
    (
        "debrid",
        "Debrid service cache",
        // debrid_cache:{service}  — availability hashes
        // debrid_checked:{service}:{media_id}  — recent-check markers
        &["debrid_cache:*", "debrid_checked:*"],
    ),
    (
        "profiles",
        "User profile data",
        // key written by crypto/profile.rs: user_profile:{uuid}
        &["user_profile:*"],
    ),
    ("events", "Sports events cache", &["events:*", "dlhd:*"]),
    ("genres", "Genre mappings", &["genres:*"]),
    ("lookup", "ID lookup cache", &["lang:*", "announce:*"]),
    (
        "scheduler",
        "Scheduler job state",
        &["scheduler:*", "apscheduler*"],
    ),
    (
        "streaming",
        "Active streaming sessions",
        // playback_url:{sha256}  — resolved CDN URLs (playback.rs)
        // setup_code:{code}      — Kodi setup codes
        // manifest:{code|hash}   — manifest cache
        &["playback_url:*", "setup_code:*", "manifest:*"],
    ),
    (
        "images",
        "Cached poster images",
        // key written by poster.rs: {media_type}_{id}.jpg  e.g. movie_tt1234567.jpg
        &["*_*.jpg"],
    ),
    (
        "rate_limit",
        "Rate limiting counters",
        &["rate_limit:*", "ratelimit:*"],
    ),
];

async fn scan_all_keys(redis: &fred::clients::Client, pattern: &str) -> Vec<String> {
    let mut all_keys: Vec<String> = Vec::new();
    let mut cursor = "0".to_string();
    loop {
        let (next_cursor, keys) = redis_scan_page(redis, &cursor, pattern, 500).await;
        all_keys.extend(keys);
        if next_cursor == "0" {
            break;
        }
        cursor = next_cursor;
    }
    all_keys
}

async fn count_pattern(redis: &fred::clients::Client, pattern: &str) -> i64 {
    scan_all_keys(redis, pattern).await.len() as i64
}

pub async fn cache_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if let Err(failure) = auth_guard::require_active_role(
        &state.pool,
        &headers,
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    {
        return auth_guard::auth_failure_response(failure).into_response();
    }

    let info_str: String = state
        .redis
        .info(Some(InfoKind::All))
        .await
        .unwrap_or_default();
    let mut info: std::collections::HashMap<&str, &str> = std::collections::HashMap::new();
    for line in info_str.lines() {
        if let Some((k, v)) = line.split_once(':') {
            info.insert(k.trim(), v.trim());
        }
    }

    let parse_i64 = |k: &str| -> i64 { info.get(k).and_then(|v| v.parse().ok()).unwrap_or(0) };
    let _parse_f64 = |k: &str| -> f64 { info.get(k).and_then(|v| v.parse().ok()).unwrap_or(0.0) };

    let hits = parse_i64("keyspace_hits");
    let misses = parse_i64("keyspace_misses");
    let hit_rate: Option<f64> = if hits + misses > 0 {
        Some(((hits as f64 / (hits + misses) as f64) * 10000.0).round() / 100.0)
    } else {
        None
    };

    let total_keys: i64 = state.redis.dbsize().await.unwrap_or(0);

    let redis_info = json!({
        "connected": true,
        "version": info.get("redis_version"),
        "memory_used": info.get("used_memory_human").unwrap_or(&"—"),
        "memory_peak": info.get("used_memory_peak_human"),
        "total_keys": total_keys,
        "connected_clients": parse_i64("connected_clients"),
        "uptime_days": parse_i64("uptime_in_days"),
        "hit_rate": hit_rate,
        "ops_per_sec": parse_i64("instantaneous_ops_per_sec"),
    });

    // Count keys for each cache type (run concurrently per category)
    let mut cache_types: Vec<JsonValue> = Vec::new();
    for (name, description, patterns) in CACHE_PATTERNS {
        let mut total: i64 = 0;
        for &pattern in *patterns {
            total += count_pattern(&state.redis, pattern).await;
        }
        let count_note: Option<&str> = if *name == "scrapers" {
            Some("This total is sorted-set members (cooldown entries), not Redis keys.")
        } else {
            None
        };
        cache_types.push(json!({
            "name": name,
            "description": description,
            "keys_count": total,
            "memory_bytes": null,
            "count_note": count_note,
        }));
    }

    Json(json!({
        "redis": redis_info,
        "cache_types": cache_types,
    }))
    .into_response()
}

#[derive(Deserialize)]
pub struct CacheKeysParamsExt {
    #[serde(default = "default_pattern")]
    pub pattern: String,
    #[serde(default = "default_count")]
    pub count: i64,
    #[serde(default = "default_cursor")]
    pub cursor: String,
    pub cache_category: Option<String>,
    pub type_filter: Option<String>,
}

async fn key_info(redis: &fred::clients::Client, key: &str) -> JsonValue {
    let key_type: String = redis
        .r#type::<String, _>(key)
        .await
        .unwrap_or_else(|_| "string".to_string());
    let ttl: i64 = redis.ttl(key).await.unwrap_or(-1);
    json!({"key": key, "type": key_type, "ttl": ttl, "size": null})
}

pub async fn cache_keys(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<CacheKeysParamsExt>,
) -> impl IntoResponse {
    if let Err(failure) = auth_guard::require_active_role(
        &state.pool,
        &headers,
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    {
        return auth_guard::auth_failure_response(failure).into_response();
    }

    let count = params.count.clamp(1, 200) as usize;

    // When cache_category is set, collect all keys for that category's patterns
    if let Some(ref cat) = params.cache_category {
        let patterns = CACHE_PATTERNS
            .iter()
            .find(|(name, _, _)| *name == cat.as_str())
            .map(|(_, _, pats)| *pats);

        if let Some(pats) = patterns {
            let mut all_keys: Vec<String> = Vec::new();
            for &pat in pats {
                let mut ks = scan_all_keys(&state.redis, pat).await;
                all_keys.append(&mut ks);
            }
            all_keys.sort();
            all_keys.dedup();

            let offset: usize = params.cursor.parse().unwrap_or(0);
            let slice: Vec<String> = all_keys.iter().skip(offset).take(count).cloned().collect();
            let total = all_keys.len();
            let has_more = offset + slice.len() < total;
            let next_cursor = if has_more {
                (offset + slice.len()).to_string()
            } else {
                "0".to_string()
            };

            let mut keys_info: Vec<JsonValue> = Vec::new();
            for k in &slice {
                keys_info.push(key_info(&state.redis, k).await);
            }

            return Json(json!({
                "keys": keys_info,
                "total": total,
                "cursor": next_cursor,
                "has_more": has_more,
            }))
            .into_response();
        }
    }

    // Default: SCAN with pattern
    let (next_cursor, raw_keys) =
        redis_scan_page(&state.redis, &params.cursor, &params.pattern, count as u32).await;

    let mut keys_info: Vec<JsonValue> = Vec::new();
    for k in &raw_keys {
        let info = key_info(&state.redis, k).await;
        if let Some(ref tf) = params.type_filter {
            if info.get("type").and_then(|v| v.as_str()) != Some(tf.as_str()) {
                continue;
            }
        }
        keys_info.push(info);
    }

    let total = keys_info.len();
    let has_more = next_cursor != "0";
    Json(json!({
        "keys": keys_info,
        "total": total,
        "cursor": next_cursor,
        "has_more": has_more,
    }))
    .into_response()
}

pub async fn cache_key_delete(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(key): Path<String>,
) -> impl IntoResponse {
    if let Err(failure) = auth_guard::require_active_role(
        &state.pool,
        &headers,
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    {
        return auth_guard::auth_failure_response(failure).into_response();
    }

    let deleted: i64 = state.redis.del(&key).await.unwrap_or(0);
    Json(CacheDeleteResponse {
        success: true,
        deleted,
    })
    .into_response()
}

#[derive(Deserialize)]
pub struct DeleteCacheItemRequest {
    pub field: Option<String>,
    pub member: Option<String>,
    pub value: Option<String>,
    pub index: Option<i64>,
}

pub async fn cache_key_item_delete(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(key): Path<String>,
    Json(body): Json<DeleteCacheItemRequest>,
) -> impl IntoResponse {
    if let Err(failure) = auth_guard::require_active_role(
        &state.pool,
        &headers,
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    {
        return auth_guard::auth_failure_response(failure).into_response();
    }

    let key_type: String = state
        .redis
        .r#type::<String, _>(&key)
        .await
        .unwrap_or_else(|_| "none".to_string());

    if key_type == "none" {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("Key '{key}' not found")})),
        )
            .into_response();
    }

    let removed: i64 = match key_type.as_str() {
        "hash" => {
            let Some(field) = body.field.filter(|f| !f.is_empty()) else {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": "Field name is required for hash type"})),
                )
                    .into_response();
            };
            state.redis.hdel(&key, field).await.unwrap_or(0)
        }
        "set" => {
            let Some(member) = body.member.filter(|m| !m.is_empty()) else {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": "Member value is required for set type"})),
                )
                    .into_response();
            };
            use fred::prelude::SetsInterface;
            state.redis.srem(&key, member).await.unwrap_or(0)
        }
        "zset" => {
            let Some(member) = body.member.filter(|m| !m.is_empty()) else {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": "Member value is required for sorted set type"})),
                )
                    .into_response();
            };
            use fred::prelude::SortedSetsInterface;
            state.redis.zrem(&key, member).await.unwrap_or(0)
        }
        "list" => {
            if let Some(index) = body.index {
                const PLACEHOLDER: &str = "__DELETED_ITEM_PLACEHOLDER__";
                use fred::prelude::ListInterface;
                if state
                    .redis
                    .lset::<(), _, _>(&key, index, PLACEHOLDER)
                    .await
                    .is_err()
                {
                    return (
                        StatusCode::NOT_FOUND,
                        Json(json!({"detail": "Item not found in the collection"})),
                    )
                        .into_response();
                }
                state.redis.lrem(&key, 1, PLACEHOLDER).await.unwrap_or(0)
            } else if let Some(value) = body.value.filter(|v| !v.is_empty()) {
                use fred::prelude::ListInterface;
                state.redis.lrem(&key, 0, value).await.unwrap_or(0)
            } else {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": "Value or index is required for list type"})),
                )
                    .into_response();
            }
        }
        other => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": format!("Cannot delete items from type '{other}'. Use DELETE /key/{{key}} instead.")})),
            )
                .into_response();
        }
    };

    if removed == 0 {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Item not found in the collection"})),
        )
            .into_response();
    }

    Json(json!({
        "success": true,
        "message": format!("Item deleted from '{key}'"),
        "removed_count": removed,
    }))
    .into_response()
}

pub async fn cache_key_get(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(key): Path<String>,
) -> impl IntoResponse {
    if let Err(failure) = auth_guard::require_active_role(
        &state.pool,
        &headers,
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    {
        return auth_guard::auth_failure_response(failure).into_response();
    }

    let key_type: String = state
        .redis
        .r#type::<String, _>(&key)
        .await
        .unwrap_or_else(|_| "string".to_string());
    let ttl: i64 = state.redis.ttl(&key).await.unwrap_or(-1);

    if key_type == "none" {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("Key '{}' not found", key)})),
        )
            .into_response();
    }

    let (value, is_binary): (JsonValue, bool) = match key_type.as_str() {
        "string" => {
            let raw: Option<Vec<u8>> = state.redis.get(&key).await.unwrap_or(None);
            match raw {
                None => (json!(null), false),
                Some(b) => match std::str::from_utf8(&b) {
                    Ok(s) => match serde_json::from_str::<JsonValue>(s) {
                        Ok(v) => (v, false),
                        Err(_) => (json!(s), false),
                    },
                    Err(_) => {
                        use base64::{Engine as _, engine::general_purpose::STANDARD};
                        (json!(STANDARD.encode(&b)), true)
                    }
                },
            }
        }
        "hash" => {
            let map: std::collections::HashMap<String, String> =
                state.redis.hgetall(&key).await.unwrap_or_default();
            (json!(map), false)
        }
        "list" => {
            let items: Vec<String> = state.redis.lrange(&key, 0, -1).await.unwrap_or_default();
            (json!(items), false)
        }
        "set" => {
            use fred::prelude::SetsInterface;
            let members: std::collections::HashSet<String> =
                state.redis.smembers(&key).await.unwrap_or_default();
            let mut v: Vec<String> = members.into_iter().collect();
            v.sort();
            (json!(v), false)
        }
        "zset" => {
            use fred::prelude::SortedSetsInterface;
            // zrange with withscores=true returns alternating member/score strings
            let raw: Vec<String> = state
                .redis
                .zrange(&key, 0i64, -1i64, None, false, None, true)
                .await
                .unwrap_or_default();
            let items: Vec<JsonValue> = raw
                .chunks(2)
                .filter_map(|c| c.first().zip(c.get(1)))
                .map(|(member, score)| {
                    let s: f64 = score.parse().unwrap_or(0.0);
                    json!({"member": member, "score": s})
                })
                .collect();
            (json!(items), false)
        }
        other => (
            json!({"_info": format!("Unsupported type: {other}")}),
            false,
        ),
    };

    Json(json!({
        "key": key,
        "type": key_type,
        "ttl": ttl,
        "value": value,
        "size": 0,
        "is_binary": is_binary,
    }))
    .into_response()
}

pub async fn cache_image_get(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(key): Path<String>,
) -> impl IntoResponse {
    if let Err(failure) = auth_guard::require_active_role(
        &state.pool,
        &headers,
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    {
        return auth_guard::auth_failure_response(failure).into_response();
    }
    let value: Option<String> = state.redis.get(&key).await.unwrap_or(None);
    Json(serde_json::json!({"key": key, "value": value})).into_response()
}

pub async fn cache_clear(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<CacheClearRequest>,
) -> impl IntoResponse {
    if let Err(failure) = auth_guard::require_active_role(
        &state.pool,
        &headers,
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    {
        return auth_guard::auth_failure_response(failure).into_response();
    }

    // Resolve the set of patterns to delete.
    // Frontend sends `type`; legacy callers may send `cache_type` or a literal `pattern`.
    let type_val = body.type_field.as_deref().unwrap_or("");
    let cache_type = if !type_val.is_empty() {
        type_val
    } else {
        body.cache_type.as_deref().unwrap_or("")
    };

    let patterns: Vec<String> = if let Some(p) = body.pattern.filter(|s| !s.is_empty()) {
        vec![p]
    } else if cache_type == "all" {
        vec!["*".to_string()]
    } else if cache_type == "pattern" {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": "pattern field required when type=pattern"})),
        )
            .into_response();
    } else if !cache_type.is_empty() {
        match cache_type_to_patterns(cache_type) {
            Some(pats) => pats.iter().map(|s| s.to_string()).collect(),
            None => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({"error": format!("Unknown cache type: {cache_type}")})),
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

    // Scan and delete for every pattern in the category.
    let mut total_cleared: i64 = 0;
    for pattern in &patterns {
        let mut cursor = "0".to_string();
        loop {
            let (next_cursor, keys) = redis_scan_page(&state.redis, &cursor, pattern, 100).await;
            if !keys.is_empty() {
                let deleted: i64 = state.redis.del(keys).await.unwrap_or(0);
                total_cleared += deleted;
            }
            cursor = next_cursor;
            if cursor == "0" {
                break;
            }
        }
    }

    Json(CacheClearResponse {
        cleared: total_cleared,
        pattern: patterns.join(", "),
    })
    .into_response()
}

pub async fn db_stats(headers: HeaderMap, State(state): State<Arc<AppState>>) -> impl IntoResponse {
    if let Err(failure) = auth_guard::require_active_role(
        &state.pool,
        &headers,
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    {
        return auth_guard::auth_failure_response(failure).into_response();
    }

    let (
        version,
        db_name,
        size_pretty,
        total_bytes,
        connection_count,
        active_queries,
        max_conn,
        deadlocks,
        commits,
        rollbacks,
        cache_hit,
        uptime,
    ) = tokio::join!(
        fetch_scalar_str(&state.pool_ro, "SELECT version()"),
        fetch_scalar_str(&state.pool_ro, "SELECT current_database()"),
        fetch_scalar_str(
            &state.pool_ro,
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        ),
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT pg_database_size(current_database())"
        ),
        // Section C: total connections to current DB (not just active)
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT count(*)::bigint FROM pg_stat_activity WHERE datname = current_database()"
        ),
        // Section C: active queries excluding self
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT count(*)::bigint FROM pg_stat_activity WHERE state='active' AND pid != pg_backend_pid()"
        ),
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT current_setting('max_connections')::int"
        ),
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT COALESCE(deadlocks, 0) FROM pg_stat_database WHERE datname = current_database()"
        ),
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT COALESCE(xact_commit, 0) FROM pg_stat_database WHERE datname = current_database()"
        ),
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT COALESCE(xact_rollback, 0) FROM pg_stat_database WHERE datname = current_database()"
        ),
        // Section C: fold cache_hit into the join
        async {
            sqlx::query_scalar::<_, f64>(
                "SELECT CASE WHEN (blks_hit + blks_read) > 0 \
                     THEN round(blks_hit::numeric / (blks_hit + blks_read) * 100, 2) \
                     ELSE 0.0 END \
                     FROM pg_stat_database WHERE datname = current_database()",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0.0)
        },
        fetch_scalar_i64(
            &state.pool_ro,
            "SELECT EXTRACT(EPOCH FROM (now() - pg_postmaster_start_time()))::bigint",
        ),
    );

    Json(DbStatsResponse {
        version,
        database_name: db_name,
        size_human: size_pretty,
        total_size_bytes: total_bytes,
        connection_count,
        max_connections: max_conn,
        cache_hit_ratio: cache_hit,
        uptime_seconds: uptime,
        active_queries,
        deadlocks,
        transactions_committed: commits,
        transactions_rolled_back: rollbacks,
    })
    .into_response()
}

pub async fn db_tables(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if let Err(failure) = auth_guard::require_active_role(
        &state.pool,
        &headers,
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    {
        return auth_guard::auth_failure_response(failure).into_response();
    }

    type TableRow = (
        String,
        String,
        i64,
        String,
        i64,
        String,
        i64,
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
    );

    let rows = sqlx::query_as::<_, TableRow>(
        r#"
        SELECT
            t.relname::text                                                          AS name,
            n.nspname::text                                                          AS schema_name,
            GREATEST(t.reltuples::bigint, COALESCE(s.n_live_tup, 0), 0)             AS row_count,
            pg_size_pretty(pg_total_relation_size(t.oid))                           AS size_human,
            pg_total_relation_size(t.oid)::bigint                                   AS size_bytes,
            pg_size_pretty(pg_indexes_size(t.oid))                                  AS index_size_human,
            pg_indexes_size(t.oid)::bigint                                          AS index_size_bytes,
            to_char(s.last_vacuum,     'YYYY-MM-DD"T"HH24:MI:SS"Z"')               AS last_vacuum,
            to_char(s.last_analyze,    'YYYY-MM-DD"T"HH24:MI:SS"Z"')               AS last_analyze,
            to_char(s.last_autovacuum, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')               AS last_autovacuum,
            to_char(s.last_autoanalyze,'YYYY-MM-DD"T"HH24:MI:SS"Z"')               AS last_autoanalyze
        FROM pg_class t
        JOIN pg_namespace n ON n.oid = t.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = t.oid
        WHERE t.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        ORDER BY pg_total_relation_size(t.oid) DESC
        LIMIT 200
        "#,
    )
    .fetch_all(&state.pool_ro)
    .await;

    match rows {
        Ok(rows) => {
            let total_bytes: i64 = rows.iter().map(|r| r.4).sum();
            let total_count = rows.len();
            let tables: Vec<TableInfo> = rows
                .into_iter()
                .map(
                    |(
                        name,
                        schema_name,
                        row_count,
                        size_human,
                        size_bytes,
                        index_size_human,
                        index_size_bytes,
                        last_vacuum,
                        last_analyze,
                        last_autovacuum,
                        last_autoanalyze,
                    )| {
                        TableInfo {
                            name,
                            schema_name,
                            row_count,
                            size_human,
                            size_bytes,
                            index_size_human,
                            index_size_bytes,
                            last_vacuum,
                            last_analyze,
                            last_autovacuum,
                            last_autoanalyze,
                        }
                    },
                )
                .collect();
            Json(TablesListResponse {
                total_count,
                total_size_bytes: total_bytes,
                total_size_human: format_bytes(total_bytes),
                tables,
            })
            .into_response()
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

fn format_bytes(bytes: i64) -> String {
    if bytes < 1024 {
        format!("{bytes} B")
    } else if bytes < 1024 * 1024 {
        format!("{:.1} KB", bytes as f64 / 1024.0)
    } else if bytes < 1024 * 1024 * 1024 {
        format!("{:.1} MB", bytes as f64 / (1024.0 * 1024.0))
    } else {
        format!("{:.2} GB", bytes as f64 / (1024.0 * 1024.0 * 1024.0))
    }
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

async fn fetch_scalar_str(pool: &sqlx::PgPool, query: &str) -> String {
    sqlx::query_scalar::<_, String>(sqlx::AssertSqlSafe(query))
        .fetch_one(pool)
        .await
        .unwrap_or_else(|_| "unknown".to_string())
}

async fn fetch_scalar_i64(pool: &sqlx::PgPool, query: &str) -> i64 {
    sqlx::query_scalar::<_, i64>(sqlx::AssertSqlSafe(query))
        .fetch_one(pool)
        .await
        .unwrap_or(0)
}
