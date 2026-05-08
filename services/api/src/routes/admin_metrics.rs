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
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::Sha256;

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

// ─── Proxy helper ─────────────────────────────────────────────────────────────

async fn proxy_to_python(
    state: &AppState,
    method: reqwest::Method,
    path: &str,
    headers: &HeaderMap,
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

// ─── Query params ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct WorkerMemoryQuery {
    pub limit: Option<i64>,
}

#[derive(Deserialize)]
pub struct ScraperHistoryQuery {
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

    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM torrent_stream")
        .fetch_one(&state.pool_ro)
        .await
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

    let rows = sqlx::query_as::<_, (Option<String>, i64)>(
        r#"SELECT s.uploader, COUNT(ts.id) as count
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
                .map(|(name, count)| {
                    json!({"name": name.unwrap_or_else(|| "Anonymous".to_string()), "count": count})
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

    let (movies, series, tv): (i64, i64, i64) = tokio::join!(
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM media WHERE type = 'movie'")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM media WHERE type = 'series'")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM media WHERE type = 'tv'")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
    );

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
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/metrics/scrapy-schedulers",
        &headers,
    )
    .await
}

/// GET /api/v1/admin/metrics/prometheus
pub async fn prometheus_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/metrics/prometheus",
        &headers,
    )
    .await
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

    let mut info_map = serde_json::Map::new();
    for line in info_str.lines() {
        if let Some((k, v)) = line.split_once(':') {
            info_map.insert(k.trim().to_string(), Value::String(v.trim().to_string()));
        }
    }

    let total_keys: i64 = state.redis.dbsize().await.unwrap_or(0);

    Json(json!({
        "timestamp": Utc::now().to_rfc3339(),
        "total_keys": total_keys,
        "info": info_map,
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
    let key = "worker_memory_metrics:history";

    let raw_entries: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(key, 0, limit - 1)
        .await
        .unwrap_or_default();

    let mut entries: Vec<Value> = Vec::new();
    for raw in &raw_entries {
        if let Ok(v) = serde_json::from_str::<Value>(raw) {
            entries.push(v);
        }
    }

    let total_entries: i64 = state.redis.llen::<i64, _>(key).await.unwrap_or(0);

    Json(json!({
        "timestamp": Utc::now().to_rfc3339(),
        "summary": {},
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

    let (total, active_daily, active_weekly, active_monthly, verified, total_profiles) = tokio::join!(
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
    );

    let role_rows =
        sqlx::query_as::<_, (String, i64)>("SELECT role, COUNT(*) FROM users GROUP BY role")
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default();

    let users_by_role: serde_json::Map<String, Value> = role_rows
        .into_iter()
        .map(|(role, count)| (role, json!(count)))
        .collect();

    Json(json!({
        "timestamp": now.to_rfc3339(),
        "total_users": total,
        "active_users": {
            "daily": active_daily,
            "weekly": active_weekly,
            "monthly": active_monthly,
        },
        "verified_users": verified,
        "unverified_users": total - verified,
        "users_by_role": users_by_role,
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

    let (total, recent, total_stream_votes, total_metadata_votes) = tokio::join!(
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
    );

    let status_rows = sqlx::query_as::<_, (String, i64)>(
        "SELECT status, COUNT(*) FROM contributions GROUP BY status",
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let contributions_by_status: serde_json::Map<String, Value> = status_rows
        .into_iter()
        .map(|(status, count)| (status, json!(count)))
        .collect();

    Json(json!({
        "timestamp": now.to_rfc3339(),
        "total_contributions": total,
        "contributions_by_status": contributions_by_status,
        "recent_contributions_week": recent,
        "total_stream_votes": total_stream_votes,
        "total_metadata_votes": total_metadata_votes,
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

    let (total_wh, recent_wh, total_rss, active_rss) = tokio::join!(
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
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/metrics/system/overview",
        &headers,
    )
    .await
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
    let key = format!("scraper_metrics:history:{scraper_name}");

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
    Query(params): Query<ScraperHistoryQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let _ = params;
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/metrics/scraper/search-runs",
        &headers,
    )
    .await
}
