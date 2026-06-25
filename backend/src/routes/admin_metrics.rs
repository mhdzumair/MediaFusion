/// Admin metrics endpoints.
///
/// Implements aggregate DB queries + Redis metrics for the admin dashboard.
/// All routes require admin JWT role.
///
/// Routes (prefix /api/v1/admin/metrics):
///   GET /torrents                             → get_torrents_count
///   GET /torrents/sources                     → get_torrents_by_sources
///   GET /torrents/uploaders                   → get_torrents_by_uploaders
///   GET /torrents/uploaders/weekly/{week_date}→ get_weekly_top_uploaders
///   GET /metadata                             → get_total_metadata
///   GET /scrapy-schedulers                    → get_schedulers_last_run
///   GET /prometheus                           → prometheus_metrics
///   GET /redis                                → redis_metrics
///   GET /workers/memory                       → get_worker_memory_metrics
///   GET /debrid-cache                         → debrid_cache_metrics
///   GET /users/stats                          → get_user_stats
///   GET /contributions/stats                  → get_contribution_stats
///   GET /activity/stats                       → get_activity_stats
///   GET /system/overview                      → get_system_overview
///   GET /scraper/latest                       → get_scraper_latest_metrics
///   GET /scraper/aggregated                   → get_scraper_aggregated_metrics
///   GET /scraper/history/{scraper_name}       → get_scraper_history
///   GET /scraper/search-runs                  → get_search_run_metrics
///   GET /scraper/history/{scraper_name}/recent→ get_scraper_recent_runs
///   GET /workers/memory                       → (same as above)
use std::sync::Arc;

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use fred::prelude::*;
use fred::types::InfoKind;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::Sha256;

use crate::cache;
use crate::state::AppState;

// ─── Auth helper ──────────────────────────────────────────────────────────────

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
    let data: Value = serde_json::from_slice(&decoded).ok()?;
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

fn forbidden() -> axum::response::Response {
    (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response()
}

// ─── Query params ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct WorkerMemoryQuery {
    pub limit: Option<i64>,
}

#[derive(Deserialize)]
pub struct ScraperHistoryQuery {
    pub limit: Option<i64>,
}

#[derive(Deserialize)]
pub struct ScraperSearchRunsQuery {
    pub query: Option<String>,
    pub meta_id: Option<String>,
    pub scraper_name: Option<String>,
    pub limit: Option<i64>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/admin/metrics/torrents
pub async fn get_torrents_count(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let count: i64 = sqlx::query_scalar(
        "SELECT GREATEST(n_live_tup, 0) FROM pg_stat_user_tables WHERE relname = 'torrent_stream'",
    )
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None)
    .unwrap_or(0);

    Json(json!({
        "total_torrents": count,
        "total_torrents_readable": count.to_string(),
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/torrents/sources
pub async fn get_torrents_by_sources(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let rows = sqlx::query_as::<_, (String, i64)>(
        r#"SELECT s.source, COUNT(ts.id) as count
           FROM torrent_stream ts
           JOIN stream s ON s.id = ts.stream_id
           GROUP BY s.source
           ORDER BY count DESC
           LIMIT 20"#,
    )
    .fetch_all(&state.pool_ro)
    .await;

    match rows {
        Ok(sources) => {
            let items: Vec<Value> = sources
                .into_iter()
                .map(|(name, count)| json!({"name": name, "count": count}))
                .collect();
            Json(items).into_response()
        }
        Err(e) => {
            tracing::error!("get_torrents_by_sources: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// GET /api/v1/admin/metrics/torrents/uploaders
pub async fn get_torrents_by_uploaders(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let rows = sqlx::query_as::<_, (Option<String>, i64)>(
        r#"SELECT s.uploader, COUNT(ts.id) as count
           FROM torrent_stream ts
           JOIN stream s ON s.id = ts.stream_id
           GROUP BY s.uploader
           ORDER BY count DESC
           LIMIT 20"#,
    )
    .fetch_all(&state.pool_ro)
    .await;

    match rows {
        Ok(uploaders) => {
            let items: Vec<Value> = uploaders
                .into_iter()
                .map(|(name, count)| {
                    json!({"name": name.unwrap_or_else(|| "Anonymous".to_string()), "count": count})
                })
                .collect();
            Json(items).into_response()
        }
        Err(e) => {
            tracing::error!("get_torrents_by_uploaders: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// GET /api/v1/admin/metrics/torrents/uploaders/weekly/{week_date}
pub async fn get_weekly_top_uploaders(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(week_date): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    // Parse the week date (YYYY-MM-DD)
    let parsed = chrono::NaiveDate::parse_from_str(&week_date, "%Y-%m-%d");
    let (week_start, week_end) = match parsed {
        Ok(d) => {
            use chrono::Datelike;
            let days_since_monday = d.weekday().num_days_from_monday() as i64;
            let monday = d - chrono::Duration::days(days_since_monday);
            let sunday = monday + chrono::Duration::days(7);
            (
                monday.format("%Y-%m-%d").to_string(),
                sunday.format("%Y-%m-%d").to_string(),
            )
        }
        Err(_) => {
            return Json(json!({"error": "Invalid date format. Please use YYYY-MM-DD format."}))
                .into_response();
        }
    };

    let rows = sqlx::query_as::<_, (Option<String>, i64, Option<chrono::DateTime<Utc>>)>(
        r#"SELECT s.uploader, COUNT(ts.id) as count, MAX(ts.uploaded_at) as latest_upload
           FROM torrent_stream ts
           JOIN stream s ON s.id = ts.stream_id
           WHERE ts.uploaded_at >= $1::date AND ts.uploaded_at < $2::date
           GROUP BY s.uploader
           ORDER BY count DESC
           LIMIT 20"#,
    )
    .bind(&week_start)
    .bind(&week_end)
    .fetch_all(&state.pool_ro)
    .await;

    match rows {
        Ok(uploaders) => {
            let items: Vec<Value> = uploaders
                .into_iter()
                .map(|(name, count, latest)| {
                    json!({
                        "name": name.unwrap_or_else(|| "Anonymous".to_string()),
                        "count": count,
                        "latest_upload": latest.map(|d| d.to_rfc3339()),
                    })
                })
                .collect();
            Json(json!({
                "week_start": week_start,
                "week_end": week_end,
                "uploaders": items,
            }))
            .into_response()
        }
        Err(e) => {
            tracing::error!("get_weekly_top_uploaders: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// GET /api/v1/admin/metrics/metadata
pub async fn get_total_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let type_rows = sqlx::query_as::<_, (crate::db::MediaType, i64)>(
        "SELECT type, COUNT(*) FROM media GROUP BY type",
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();
    let mut movies = 0i64;
    let mut series = 0i64;
    let mut tv = 0i64;
    for (t, c) in &type_rows {
        match t {
            crate::db::MediaType::Movie => movies = *c,
            crate::db::MediaType::Series => series = *c,
            crate::db::MediaType::Tv => tv = *c,
            crate::db::MediaType::Events => {}
        }
    }

    Json(json!({
        "movies": movies,
        "series": series,
        "tv_channels": tv,
        "total": movies + series + tv,
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/scrapy-schedulers
pub async fn get_schedulers_last_run(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let mut schedulers: Vec<Value> = Vec::new();
    let mut cursor = "0".to_string();
    loop {
        let result: Result<fred::types::Value, _> = state
            .redis
            .scan_page(cursor.clone(), "scheduler_last_run:*", Some(100), None)
            .await;
        match result {
            Ok(fred::types::Value::Array(ref arr)) if arr.len() == 2 => {
                cursor = match &arr[0] {
                    fred::types::Value::String(s) => s.to_string(),
                    fred::types::Value::Bytes(b) => String::from_utf8_lossy(b).to_string(),
                    fred::types::Value::Integer(n) => n.to_string(),
                    _ => "0".to_string(),
                };
                if let fred::types::Value::Array(key_arr) = &arr[1] {
                    for k in key_arr {
                        let key_str = match k {
                            fred::types::Value::String(s) => s.to_string(),
                            fred::types::Value::Bytes(b) => String::from_utf8_lossy(b).to_string(),
                            _ => continue,
                        };
                        let name = key_str
                            .strip_prefix("scheduler_last_run:")
                            .unwrap_or(&key_str)
                            .to_string();
                        let val: Option<String> = state.redis.get(&key_str).await.unwrap_or(None);
                        schedulers.push(json!({"name": name, "last_run": val}));
                    }
                }
            }
            _ => break,
        }
        if cursor == "0" {
            break;
        }
    }

    Json(json!({"schedulers": schedulers})).into_response()
}

/// GET /api/v1/admin/metrics/prometheus
pub async fn prometheus_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    use axum::http::header::CONTENT_TYPE;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    // Use pg_stat_user_tables estimates — accurate within autovacuum cycle, never a full scan
    let (media_count, stream_count): (i64, i64) = tokio::join!(
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT GREATEST(n_live_tup, 0) FROM pg_stat_user_tables WHERE relname = 'media'",
            )
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
            .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT GREATEST(n_live_tup, 0) FROM pg_stat_user_tables WHERE relname = 'stream'",
            )
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
            .unwrap_or(0)
        },
    );

    let body = format!(
        "# HELP mediafusion_media_total Total number of media items\n\
         # TYPE mediafusion_media_total gauge\n\
         mediafusion_media_total {media_count}\n\
         # HELP mediafusion_streams_total Total number of streams\n\
         # TYPE mediafusion_streams_total gauge\n\
         mediafusion_streams_total {stream_count}\n"
    );

    axum::response::Response::builder()
        .header(CONTENT_TYPE, "text/plain; version=0.0.4")
        .body(axum::body::Body::from(body))
        .unwrap()
}

/// GET /api/v1/admin/metrics/redis
pub async fn redis_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let info_str: String = state
        .redis
        .info(Some(InfoKind::All))
        .await
        .unwrap_or_default();

    let mut info: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for line in info_str.lines() {
        if let Some((k, v)) = line.split_once(':') {
            info.insert(k.trim().to_string(), v.trim().to_string());
        }
    }

    let parse_i64 = |k: &str| -> Option<i64> { info.get(k)?.parse().ok() };
    let parse_f64 = |k: &str| -> Option<f64> { info.get(k)?.parse().ok() };

    let hits = parse_i64("keyspace_hits").unwrap_or(0);
    let misses = parse_i64("keyspace_misses").unwrap_or(0);
    let hit_rate = if hits + misses > 0 {
        hits as f64 / (hits + misses) as f64 * 100.0
    } else {
        0.0
    };

    Json(json!({
        "timestamp": Utc::now().to_rfc3339(),
        "app_pool_stats": {},
        "memory": {
            "used_memory_human": info.get("used_memory_human"),
            "used_memory_peak_human": info.get("used_memory_peak_human"),
            "maxmemory_human": info.get("maxmemory_human"),
            "mem_fragmentation_ratio": parse_f64("mem_fragmentation_ratio"),
        },
        "connections": {
            "connected_clients": parse_i64("connected_clients"),
            "blocked_clients": parse_i64("blocked_clients"),
            "maxclients": parse_i64("maxclients"),
        },
        "performance": {
            "instantaneous_ops_per_sec": parse_i64("instantaneous_ops_per_sec"),
            "total_commands_processed": parse_i64("total_commands_processed"),
        },
        "cache": {
            "keyspace_hits": hits,
            "keyspace_misses": misses,
            "hit_rate": hit_rate,
        },
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/workers/memory
pub async fn get_worker_memory_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<WorkerMemoryQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let limit = params.limit.unwrap_or(200).clamp(1, 1000);
    let history_key = "worker_metrics:memory:history";
    let summary_key = "worker_metrics:memory:summary";

    let (raw_entries, summary_raw, total_entries): (
        Vec<String>,
        std::collections::HashMap<String, String>,
        i64,
    ) = tokio::join!(
        async {
            state
                .redis
                .lrange::<Vec<String>, _>(history_key, 0, limit - 1)
                .await
                .unwrap_or_default()
        },
        async {
            state
                .redis
                .hgetall::<std::collections::HashMap<String, String>, _>(summary_key)
                .await
                .unwrap_or_default()
        },
        async { state.redis.llen::<i64, _>(history_key).await.unwrap_or(0) },
    );

    let mut entries: Vec<Value> = Vec::new();
    for raw in &raw_entries {
        if let Ok(v) = serde_json::from_str::<Value>(raw) {
            entries.push(v);
        }
    }

    let parse_int =
        |k: &str| -> i64 { summary_raw.get(k).and_then(|v| v.parse().ok()).unwrap_or(0) };

    let mut status_counts = serde_json::Map::new();
    let mut actor_counts = serde_json::Map::new();
    let mut error_counts = serde_json::Map::new();
    for (k, v) in &summary_raw {
        let n: i64 = v.parse().unwrap_or(0);
        if let Some(name) = k.strip_prefix("status:") {
            status_counts.insert(name.to_string(), json!(n));
        } else if let Some(name) = k.strip_prefix("actor:") {
            actor_counts.insert(name.to_string(), json!(n));
        } else if let Some(name) = k.strip_prefix("error:") {
            error_counts.insert(name.to_string(), json!(n));
        }
    }

    Json(json!({
        "timestamp": Utc::now().to_rfc3339(),
        "summary": {
            "total_events": parse_int("total_events"),
            "status_counts": status_counts,
            "actor_counts": actor_counts,
            "error_counts": error_counts,
            "last_timestamp": summary_raw.get("last_timestamp"),
            "last_actor": summary_raw.get("last_actor"),
            "last_status": summary_raw.get("last_status"),
            "last_rss_bytes": parse_int("last_rss_bytes"),
            "peak_rss_bytes": parse_int("peak_rss_bytes"),
        },
        "entries": entries,
        "total_entries": total_entries,
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/debrid-cache
pub async fn debrid_cache_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let services = [
        "alldebrid",
        "debridlink",
        "offcloud",
        "pikpak",
        "premiumize",
        "qbittorrent",
        "realdebrid",
        "seedr",
        "torbox",
        "easydebrid",
        "debrider",
    ];

    let mut service_data = serde_json::Map::new();
    for service in &services {
        let key = format!("debrid_cache:{service}");
        let size: i64 = state.redis.hlen::<i64, _>(&key).await.unwrap_or(0);
        if size > 0 {
            service_data.insert(service.to_string(), json!({"cached_torrents": size}));
        }
    }

    Json(json!({
        "timestamp": Utc::now().to_rfc3339(),
        "services": service_data,
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/users/stats
pub async fn get_user_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let now = Utc::now();
    let day_ago = now - chrono::Duration::hours(24);
    let week_ago = now - chrono::Duration::days(7);
    let month_ago = now - chrono::Duration::days(30);

    let (
        total,
        active_daily,
        active_weekly,
        active_monthly,
        verified,
        total_profiles,
        new_users_this_week,
    ) = tokio::join!(
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users WHERE last_login >= $1")
                .bind(day_ago)
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users WHERE last_login >= $1")
                .bind(week_ago)
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users WHERE last_login >= $1")
                .bind(month_ago)
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users WHERE is_verified = true")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM user_profiles")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users WHERE created_at >= $1")
                .bind(week_ago)
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
    );

    let (role_rows, contribution_level_rows) = tokio::join!(
        async {
            sqlx::query_as::<_, (String, i64)>("SELECT role, COUNT(*) FROM users GROUP BY role")
                .fetch_all(&state.pool_ro)
                .await
                .unwrap_or_default()
        },
        async {
            sqlx::query_as::<_, (String, i64)>(
                "SELECT contribution_level, COUNT(*) FROM users GROUP BY contribution_level",
            )
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default()
        },
    );

    let users_by_role: serde_json::Map<String, Value> = role_rows
        .into_iter()
        .map(|(role, count)| (role, json!(count)))
        .collect();

    let users_by_contribution_level: serde_json::Map<String, Value> = contribution_level_rows
        .into_iter()
        .map(|(level, count)| (level, json!(count)))
        .collect();

    Json(json!({
        "timestamp": now.to_rfc3339(),
        "total_users": total,
        "active_users": {
            "daily": active_daily,
            "weekly": active_weekly,
            "monthly": active_monthly,
        },
        "new_users_this_week": new_users_this_week,
        "verified_users": verified,
        "unverified_users": total - verified,
        "users_by_role": users_by_role,
        "users_by_contribution_level": users_by_contribution_level,
        "total_profiles": total_profiles,
        "avg_profiles_per_user": if total > 0 { (total_profiles as f64 / total as f64 * 100.0).round() / 100.0 } else { 0.0 },
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/contributions/stats
pub async fn get_contribution_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let now = Utc::now();
    let week_ago = now - chrono::Duration::days(7);

    let (total, recent, total_stream_votes, total_metadata_votes, unique_contributors) = tokio::join!(
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM contributions")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM contributions WHERE created_at >= $1",
            )
            .bind(week_ago)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM stream_votes")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM metadata_votes")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(DISTINCT user_id) FROM contributions")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
    );

    let status_rows = sqlx::query_as::<_, (String, i64)>(
        "SELECT status, COUNT(*) FROM contributions GROUP BY status",
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let contributions_by_status: serde_json::Map<String, Value> = status_rows
        .into_iter()
        .map(|(status, count)| (status.clone(), json!(count)))
        .collect();

    let pending_review = contributions_by_status
        .get("pending")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);

    Json(json!({
        "timestamp": now.to_rfc3339(),
        "total_contributions": total,
        "contributions_by_status": contributions_by_status,
        "pending_review": pending_review,
        "recent_contributions_week": recent,
        "total_stream_votes": total_stream_votes,
        "total_metadata_votes": total_metadata_votes,
        "unique_contributors": unique_contributors,
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/activity/stats
pub async fn get_activity_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let now = Utc::now();
    let week_ago = now - chrono::Duration::days(7);

    let (
        total_wh,
        recent_wh,
        unique_watchers,
        total_downloads,
        total_library,
        total_playback,
        total_plays,
        total_rss,
        active_rss,
    ) = tokio::join!(
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM watch_history")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM watch_history WHERE watched_at >= $1",
            )
            .bind(week_ago)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(DISTINCT user_id) FROM watch_history")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM watch_history WHERE action = 'DOWNLOADED'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM user_library_item")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM playback_tracking")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT COALESCE(SUM(play_count), 0) FROM playback_tracking",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM rss_feeds")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM rss_feeds WHERE is_active = true")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
    );

    Json(json!({
        "timestamp": now.to_rfc3339(),
        "watch_history": {
            "total_entries": total_wh,
            "recent_week": recent_wh,
            "unique_users": unique_watchers,
        },
        "downloads": {
            "total": total_downloads,
        },
        "library": {
            "total_items": total_library,
        },
        "playback": {
            "total_entries": total_playback,
            "total_plays": total_plays,
        },
        "rss_feeds": {
            "total": total_rss,
            "active": active_rss,
        },
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/system/overview
pub async fn get_system_overview(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    const CACHE_KEY: &str = "admin:system:overview";
    if let Some(cached) = cache::get_json(&state.redis, CACHE_KEY).await {
        return Json(cached).into_response();
    }

    let now = Utc::now();

    // Stream counts by type
    let stream_rows = sqlx::query_as::<_, (crate::db::StreamType, i64)>(
        "SELECT stream_type, COUNT(*) FROM stream GROUP BY stream_type",
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut by_type = serde_json::Map::new();
    let mut total_streams: i64 = 0;
    for (stype, cnt) in &stream_rows {
        by_type.insert(stype.as_wire().to_lowercase(), json!(cnt));
        total_streams += cnt;
    }
    // Ensure all known types are present
    for t in &[
        "torrent",
        "http",
        "youtube",
        "usenet",
        "telegram",
        "external_link",
        "acestream",
    ] {
        by_type.entry(*t).or_insert(json!(0));
    }

    // Metadata counts
    let media_type_rows = sqlx::query_as::<_, (crate::db::MediaType, i64)>(
        "SELECT type, COUNT(*) FROM media GROUP BY type",
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();
    let mut movies = 0i64;
    let mut series = 0i64;
    let mut tv = 0i64;
    for (t, c) in &media_type_rows {
        match t {
            crate::db::MediaType::Movie => movies = *c,
            crate::db::MediaType::Series => series = *c,
            crate::db::MediaType::Tv => tv = *c,
            crate::db::MediaType::Events => {}
        }
    }

    // User counts
    let (total_users, active_today): (i64, i64) = tokio::join!(
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM users WHERE last_login >= NOW() - INTERVAL '24 hours'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0)
        },
    );

    // Pending contributions
    let pending_contributions: i64 =
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM contributions WHERE status = 'PENDING'")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

    let formatted = if total_streams >= 1_000_000_000 {
        format!("{:.1} billion", total_streams as f64 / 1_000_000_000.0)
    } else if total_streams >= 1_000_000 {
        format!("{:.1} million", total_streams as f64 / 1_000_000.0)
    } else if total_streams >= 1_000 {
        format!("{:.1} thousand", total_streams as f64 / 1_000.0)
    } else {
        total_streams.to_string()
    };

    let result = json!({
        "timestamp": now.to_rfc3339(),
        "streams": {
            "total": total_streams,
            "formatted": formatted,
            "by_type": by_type,
        },
        "content": {
            "total": movies + series + tv,
            "movies": movies,
            "series": series,
            "tv_channels": tv,
        },
        "users": {
            "total": total_users,
            "active_today": active_today,
        },
        "moderation": {
            "pending_contributions": pending_contributions,
        },
    });
    cache::set_json(&state.redis, CACHE_KEY, &result, 30).await;
    Json(result).into_response()
}

/// GET /api/v1/admin/metrics/scraper/latest
pub async fn get_scraper_latest_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let key = "scraper_metrics:latest";
    let raw: Option<String> = state
        .redis
        .get::<Option<String>, _>(key)
        .await
        .unwrap_or(None);

    match raw {
        Some(s) => match serde_json::from_str::<Value>(&s) {
            Ok(v) => Json(v).into_response(),
            Err(_) => Json(
                json!({"scrapers": [], "total_scrapers": 0, "timestamp": Utc::now().to_rfc3339()}),
            )
            .into_response(),
        },
        None => {
            Json(json!({"scrapers": [], "total_scrapers": 0, "timestamp": Utc::now().to_rfc3339()}))
                .into_response()
        }
    }
}

/// GET /api/v1/admin/metrics/scraper/aggregated
pub async fn get_scraper_aggregated_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let key = "scraper_metrics:aggregated";
    let raw: Option<String> = state
        .redis
        .get::<Option<String>, _>(key)
        .await
        .unwrap_or(None);

    match raw {
        Some(s) => match serde_json::from_str::<Value>(&s) {
            Ok(v) => Json(v).into_response(),
            Err(_) => Json(json!({})).into_response(),
        },
        None => Json(json!({})).into_response(),
    }
}

/// GET /api/v1/admin/metrics/scraper/history/{scraper_name}
pub async fn get_scraper_history(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(scraper_name): Path<String>,
    Query(params): Query<ScraperHistoryQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let limit = params.limit.unwrap_or(20).clamp(1, 100);
    let key = format!("scraper_metrics_history:{scraper_name}");

    let raw_entries: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(&key, 0, limit - 1)
        .await
        .unwrap_or_default();

    let mut history: Vec<Value> = Vec::new();
    for raw in &raw_entries {
        if let Ok(v) = serde_json::from_str::<Value>(raw) {
            history.push(v);
        }
    }

    Json(json!({
        "scraper_name": scraper_name,
        "history": history,
        "total": history.len(),
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/scraper/search-runs
pub async fn get_search_run_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ScraperSearchRunsQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let limit = params.limit.unwrap_or(100).clamp(1, 500);
    let per_scraper_limit = limit.clamp(25, 100);

    // Discover all scraper keys via SCAN (fred has no KEYS command)
    const HISTORY_PREFIX: &str = "scraper_metrics_history:";
    let mut all_keys: Vec<String> = Vec::new();
    let mut cursor = "0".to_string();
    loop {
        let result: Result<fred::types::Value, _> = state
            .redis
            .scan_page(
                cursor.clone(),
                format!("{HISTORY_PREFIX}*"),
                Some(100),
                None,
            )
            .await;
        match result {
            Ok(fred::types::Value::Array(arr)) if arr.len() == 2 => {
                cursor = match &arr[0] {
                    fred::types::Value::String(s) => s.to_string(),
                    fred::types::Value::Bytes(b) => String::from_utf8_lossy(b).to_string(),
                    fred::types::Value::Integer(n) => n.to_string(),
                    _ => "0".to_string(),
                };
                if let fred::types::Value::Array(key_arr) = &arr[1] {
                    for k in key_arr {
                        match k {
                            fred::types::Value::String(s) => all_keys.push(s.to_string()),
                            fred::types::Value::Bytes(b) => {
                                all_keys.push(String::from_utf8_lossy(b).to_string())
                            }
                            _ => {}
                        }
                    }
                }
            }
            _ => break,
        }
        if cursor == "0" {
            break;
        }
    }

    let scraper_filter = params.scraper_name.as_deref().map(|s| s.to_lowercase());
    let meta_id_filter = params.meta_id.as_deref().map(|s| s.to_lowercase());
    let query_filter = params.query.as_deref().map(|s| s.trim().to_lowercase());

    let mut runs: Vec<Value> = Vec::new();

    for key in &all_keys {
        let scraper_name = key.strip_prefix(HISTORY_PREFIX).unwrap_or(key.as_str());
        if let Some(ref sf) = scraper_filter
            && scraper_name.to_lowercase() != *sf {
                continue;
            }

        use fred::prelude::ListInterface;
        let raw_entries: Vec<String> = state
            .redis
            .lrange::<Vec<String>, _>(key.as_str(), 0, per_scraper_limit - 1)
            .await
            .unwrap_or_default();

        for raw in &raw_entries {
            let Ok(entry) = serde_json::from_str::<Value>(raw) else {
                continue;
            };

            if let Some(ref mf) = meta_id_filter {
                let entry_meta = entry
                    .get("meta_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_lowercase();
                if &entry_meta != mf {
                    continue;
                }
            }

            if let Some(ref qf) = query_filter {
                let blob = format!(
                    "{} {} {}",
                    entry
                        .get("scraper_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or(""),
                    entry.get("meta_id").and_then(|v| v.as_str()).unwrap_or(""),
                    entry
                        .get("meta_title")
                        .and_then(|v| v.as_str())
                        .unwrap_or(""),
                )
                .to_lowercase();
                if !blob.contains(qf.as_str()) {
                    continue;
                }
            }

            runs.push(entry);
        }
    }

    runs.sort_by(|a, b| {
        let ta = a.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
        let tb = b.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
        tb.cmp(ta)
    });
    runs.truncate(limit as usize);
    let total = runs.len();

    Json(json!({ "runs": runs, "total": total })).into_response()
}

/// GET /api/v1/admin/metrics/scrapy-schedulers
pub async fn get_scrapy_schedulers(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl axum::response::IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::FORBIDDEN,
            axum::Json(json!({"error": "Forbidden"})),
        )
            .into_response();
    }

    let mut results: Vec<Value> = Vec::new();
    let mut cursor = "0".to_string();
    loop {
        let result: Result<fred::types::Value, _> = state
            .redis
            .scan_page(cursor.clone(), "scraper_metrics_latest:*", Some(100), None)
            .await;
        match result {
            Ok(fred::types::Value::Array(arr)) if arr.len() == 2 => {
                cursor = match &arr[0] {
                    fred::types::Value::String(s) => s.to_string(),
                    fred::types::Value::Bytes(b) => String::from_utf8_lossy(b).to_string(),
                    fred::types::Value::Integer(n) => n.to_string(),
                    _ => "0".to_string(),
                };
                if let fred::types::Value::Array(key_arr) = &arr[1] {
                    for k in key_arr {
                        let key_str = match k {
                            fred::types::Value::String(s) => s.to_string(),
                            fred::types::Value::Bytes(b) => String::from_utf8_lossy(b).to_string(),
                            _ => continue,
                        };
                        let val: Option<String> = state.redis.get(&key_str).await.unwrap_or(None);
                        if let Some(s) = val
                            && let Ok(v) = serde_json::from_str::<Value>(&s) {
                                results.push(v);
                            }
                    }
                }
            }
            _ => break,
        }
        if cursor == "0" {
            break;
        }
    }

    Json(json!({"schedulers": results})).into_response()
}

/// GET /api/v1/admin/metrics/scrapers/{scraper_name}
pub async fn get_scraper_by_name(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(scraper_name): Path<String>,
) -> impl axum::response::IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::FORBIDDEN,
            axum::Json(json!({"error": "Forbidden"})),
        )
            .into_response();
    }

    let aggregated_key = format!("scraper_metrics_aggregated:{scraper_name}");
    let history_key = format!("scraper_metrics_history:{scraper_name}");

    let aggregated_raw: Option<String> = state.redis.get(&aggregated_key).await.unwrap_or(None);
    let aggregated: Value = aggregated_raw
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .unwrap_or(Value::Null);

    use fred::prelude::ListInterface;
    let history_raw: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(&history_key, 0, 99)
        .await
        .unwrap_or_default();
    let history: Vec<Value> = history_raw
        .iter()
        .filter_map(|s| serde_json::from_str::<Value>(s).ok())
        .collect();

    Json(json!({
        "scraper_name": scraper_name,
        "aggregated": aggregated,
        "history": history,
    }))
    .into_response()
}

/// DELETE /api/v1/admin/metrics/scrapers/{scraper_name}
pub async fn delete_scraper_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(scraper_name): Path<String>,
) -> impl axum::response::IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::FORBIDDEN,
            axum::Json(json!({"error": "Forbidden"})),
        )
            .into_response();
    }

    let key_latest = format!("scraper_metrics_latest:{scraper_name}");
    let key_history = format!("scraper_metrics_history:{scraper_name}");
    let key_aggregated = format!("scraper_metrics_aggregated:{scraper_name}");

    let _: Result<(), _> = state
        .redis
        .del(vec![key_latest, key_history, key_aggregated])
        .await;

    Json(json!({
        "status": "success",
        "message": format!("Scraper metrics deleted for {scraper_name}"),
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/scrapers/{scraper_name}/history
pub async fn get_scraper_name_history(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(scraper_name): Path<String>,
) -> impl axum::response::IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::FORBIDDEN,
            axum::Json(json!({"error": "Forbidden"})),
        )
            .into_response();
    }

    let key = format!("scraper_metrics_history:{scraper_name}");
    use fred::prelude::ListInterface;
    let items_raw: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(&key, 0, -1)
        .await
        .unwrap_or_default();
    let history: Vec<Value> = items_raw
        .iter()
        .filter_map(|s| serde_json::from_str::<Value>(s).ok())
        .collect();

    Json(json!({
        "scraper_name": scraper_name,
        "history": history,
    }))
    .into_response()
}

/// GET /api/v1/admin/metrics/scrapers/{scraper_name}/latest
pub async fn get_scraper_name_latest(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(scraper_name): Path<String>,
) -> impl axum::response::IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::FORBIDDEN,
            axum::Json(json!({"error": "Forbidden"})),
        )
            .into_response();
    }

    let key = format!("scraper_metrics_latest:{scraper_name}");
    let raw: Option<String> = state.redis.get(&key).await.unwrap_or(None);

    match raw.and_then(|s| serde_json::from_str::<Value>(&s).ok()) {
        Some(v) => Json(v).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "No latest metrics found for this scraper"})),
        )
            .into_response(),
    }
}

/// GET /api/v1/admin/metrics/scrapers/{scraper_name}/metrics
pub async fn get_scraper_name_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(scraper_name): Path<String>,
) -> impl axum::response::IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::FORBIDDEN,
            axum::Json(json!({"error": "Forbidden"})),
        )
            .into_response();
    }

    let key = format!("scraper_metrics_aggregated:{scraper_name}");
    let raw: Option<String> = state.redis.get(&key).await.unwrap_or(None);

    match raw.and_then(|s| serde_json::from_str::<Value>(&s).ok()) {
        Some(v) => Json(v).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "No aggregated metrics found for this scraper"})),
        )
            .into_response(),
    }
}
