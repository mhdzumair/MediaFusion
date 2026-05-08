/// Admin scraper, scheduler, task, and telegram management endpoints.
///
/// All routes use the PROXY PATTERN: Rust validates admin JWT, then forwards
/// the request to the Python worker service. If Python is unavailable, returns 503.
///
/// Source files:
///   scrapers.py           (15 endpoints, prefix /api/v1/admin/scrapers)
///   scheduler_management.py (6 endpoints, prefix /api/v1/admin)
///   task_management.py    (8 endpoints,  prefix /api/v1/admin/tasks)
///   telegram_admin.py     (4 endpoints,  prefix /telegram)
///
/// Routes:
/// ── Scrapers (/api/v1/admin/scrapers) ──────────────────────────────────────
///   GET  /spiders                        → list_spiders
///   POST /run                            → run_scraper
///   POST /block-torrent                  → block_torrent
///   POST /unblock-torrent                → unblock_torrent
///   GET  /catalogs                       → get_catalog_data
///   GET  /status                         → get_scraper_status
///   GET  /dmm-hashlist/status            → get_dmm_hashlist_status
///   POST /dmm-hashlist/run               → run_dmm_hashlist
///   POST /dmm-hashlist/run-full          → run_dmm_hashlist_full
///   POST /migrate-media                  → migrate_media
///   POST /migrate-id                     → migrate_id
///   POST /update-images                  → update_media_images
///   GET  /update-imdb/{meta_id}          → refresh_imdb_data
///   DELETE /torrent/{info_hash}          → delete_torrent
///   POST /add-tv-metadata                → add_tv_metadata
///
/// ── Schedulers (/api/v1/admin) ─────────────────────────────────────────────
///   GET  /schedulers                     → list_schedulers
///   GET  /schedulers/stats               → get_scheduler_stats
///   GET  /schedulers/{job_id}            → get_scheduler_job
///   POST /schedulers/{job_id}/run        → run_scheduler_job
///   POST /schedulers/{job_id}/run-inline → run_scheduler_job_inline
///   GET  /schedulers/{job_id}/history    → get_job_history
///
/// ── Tasks (/api/v1/admin/tasks) ────────────────────────────────────────────
///   GET  /overview                       → get_task_overview
///   GET  /                               → list_tasks
///   GET  /stream                         → stream_task_snapshots
///   POST /bulk-cancel                    → bulk_cancel_tasks
///   POST /bulk-retry                     → bulk_retry_tasks
///   GET  /{task_id}                      → get_task_detail
///   POST /{task_id}/retry                → retry_task
///   POST /{task_id}/cancel               → cancel_task
///
/// ── Telegram (/api/v1/admin/telegram) ──────────────────────────────────────
///   GET  /stats                          → get_telegram_stats
///   POST /migrate                        → migrate_single_stream
///   POST /migrate/bulk                   → migrate_bulk_streams
///   GET  /exportable                     → get_exportable_streams

use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helpers ─────────────────────────────────────────────────────────────

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

fn validate_moderator_or_admin(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
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
    let role = data["role"].as_str().unwrap_or("user");
    if role != "admin" && role != "moderator" {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

fn forbidden() -> axum::response::Response {
    (
        StatusCode::FORBIDDEN,
        Json(json!({"detail": "Forbidden"})),
    )
        .into_response()
}

// ─── Proxy helper ─────────────────────────────────────────────────────────────

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

async fn proxy_to_python_with_query(
    state: &AppState,
    method: reqwest::Method,
    path: &str,
    headers: &HeaderMap,
    body: Option<Value>,
) -> axum::response::Response {
    // path already contains query string; delegate to main helper
    proxy_to_python(state, method, path, headers, body).await
}

// ─────────────────────────────────────────────────────────────────────────────
// SCRAPERS  /api/v1/admin/scrapers
// ─────────────────────────────────────────────────────────────────────────────

/// GET /api/v1/admin/scrapers/spiders
pub async fn list_spiders(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/scrapers/spiders",
        &headers,
        None,
    )
    .await
}

/// POST /api/v1/admin/scrapers/run
pub async fn run_scraper(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/scrapers/run",
        &headers,
        Some(body),
    )
    .await
}

/// POST /api/v1/admin/scrapers/block-torrent
pub async fn block_torrent(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/scrapers/block-torrent",
        &headers,
        Some(body),
    )
    .await
}

/// POST /api/v1/admin/scrapers/unblock-torrent
pub async fn unblock_torrent(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/scrapers/unblock-torrent",
        &headers,
        Some(body),
    )
    .await
}

/// GET /api/v1/admin/scrapers/catalogs
pub async fn get_catalog_data(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/scrapers/catalogs",
        &headers,
        None,
    )
    .await
}

/// GET /api/v1/admin/scrapers/status
pub async fn get_scraper_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/scrapers/status",
        &headers,
        None,
    )
    .await
}

/// GET /api/v1/admin/scrapers/dmm-hashlist/status
pub async fn get_dmm_hashlist_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/scrapers/dmm-hashlist/status",
        &headers,
        None,
    )
    .await
}

/// POST /api/v1/admin/scrapers/dmm-hashlist/run
pub async fn run_dmm_hashlist(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/scrapers/dmm-hashlist/run",
        &headers,
        Some(body),
    )
    .await
}

/// POST /api/v1/admin/scrapers/dmm-hashlist/run-full
pub async fn run_dmm_hashlist_full(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/scrapers/dmm-hashlist/run-full",
        &headers,
        Some(body),
    )
    .await
}

/// POST /api/v1/admin/scrapers/migrate-media
/// Note: Python allows moderator or admin role for this endpoint.
pub async fn migrate_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_moderator_or_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/scrapers/migrate-media",
        &headers,
        Some(body),
    )
    .await
}

/// POST /api/v1/admin/scrapers/migrate-id
pub async fn migrate_id(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/scrapers/migrate-id",
        &headers,
        Some(body),
    )
    .await
}

/// POST /api/v1/admin/scrapers/update-images
pub async fn update_media_images(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/scrapers/update-images",
        &headers,
        Some(body),
    )
    .await
}

/// GET /api/v1/admin/scrapers/update-imdb/{meta_id}?media_type=...
#[derive(Deserialize)]
pub struct RefreshImdbQuery {
    pub media_type: Option<String>,
}

pub async fn refresh_imdb_data(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(meta_id): Path<String>,
    Query(params): Query<RefreshImdbQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = format!("/api/v1/admin/scrapers/update-imdb/{meta_id}");
    if let Some(ref mt) = params.media_type {
        path.push_str(&format!("?media_type={}", urlencoding::encode(mt)));
    }
    proxy_to_python_with_query(&state, reqwest::Method::GET, &path, &headers, None).await
}

/// DELETE /api/v1/admin/scrapers/torrent/{info_hash}?reason=...
#[derive(Deserialize)]
pub struct DeleteTorrentQuery {
    pub reason: Option<String>,
}

pub async fn delete_torrent(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(info_hash): Path<String>,
    Query(params): Query<DeleteTorrentQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = format!("/api/v1/admin/scrapers/torrent/{info_hash}");
    if let Some(ref r) = params.reason {
        path.push_str(&format!("?reason={}", urlencoding::encode(r)));
    }
    proxy_to_python_with_query(&state, reqwest::Method::DELETE, &path, &headers, None).await
}

/// POST /api/v1/admin/scrapers/add-tv-metadata
pub async fn add_tv_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/scrapers/add-tv-metadata",
        &headers,
        Some(body),
    )
    .await
}

// ─────────────────────────────────────────────────────────────────────────────
// SCHEDULERS  /api/v1/admin/schedulers
// ─────────────────────────────────────────────────────────────────────────────

/// GET /api/v1/admin/schedulers?category=...&enabled_only=...
#[derive(Deserialize)]
pub struct ListSchedulersQuery {
    pub category: Option<String>,
    pub enabled_only: Option<bool>,
}

pub async fn list_schedulers(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListSchedulersQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = "/api/v1/admin/schedulers?".to_string();
    if let Some(ref c) = params.category {
        path.push_str(&format!("category={}&", urlencoding::encode(c)));
    }
    if let Some(eo) = params.enabled_only {
        path.push_str(&format!("enabled_only={eo}&"));
    }
    proxy_to_python_with_query(
        &state,
        reqwest::Method::GET,
        path.trim_end_matches('&'),
        &headers,
        None,
    )
    .await
}

/// GET /api/v1/admin/schedulers/stats
pub async fn get_scheduler_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/schedulers/stats",
        &headers,
        None,
    )
    .await
}

/// GET /api/v1/admin/schedulers/{job_id}
pub async fn get_scheduler_job(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/schedulers/{job_id}");
    proxy_to_python(&state, reqwest::Method::GET, &path, &headers, None).await
}

/// POST /api/v1/admin/schedulers/{job_id}/run?force_run=...
#[derive(Deserialize)]
pub struct RunSchedulerQuery {
    pub force_run: Option<bool>,
}

pub async fn run_scheduler_job(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    Query(params): Query<RunSchedulerQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = format!("/api/v1/admin/schedulers/{job_id}/run");
    if let Some(fr) = params.force_run {
        path.push_str(&format!("?force_run={fr}"));
    }
    proxy_to_python_with_query(&state, reqwest::Method::POST, &path, &headers, None).await
}

/// POST /api/v1/admin/schedulers/{job_id}/run-inline
pub async fn run_scheduler_job_inline(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/schedulers/{job_id}/run-inline");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, None).await
}

/// GET /api/v1/admin/schedulers/{job_id}/history?limit=...
#[derive(Deserialize)]
pub struct JobHistoryQuery {
    pub limit: Option<i64>,
}

pub async fn get_job_history(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    Query(params): Query<JobHistoryQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = format!("/api/v1/admin/schedulers/{job_id}/history");
    if let Some(l) = params.limit {
        path.push_str(&format!("?limit={l}"));
    }
    proxy_to_python_with_query(&state, reqwest::Method::GET, &path, &headers, None).await
}

// ─────────────────────────────────────────────────────────────────────────────
// TASKS  /api/v1/admin/tasks
// ─────────────────────────────────────────────────────────────────────────────

/// GET /api/v1/admin/tasks/overview?sample_size=...
#[derive(Deserialize)]
pub struct TaskOverviewQuery {
    pub sample_size: Option<i64>,
}

pub async fn get_task_overview(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<TaskOverviewQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = "/api/v1/admin/tasks/overview".to_string();
    if let Some(ss) = params.sample_size {
        path.push_str(&format!("?sample_size={ss}"));
    }
    proxy_to_python_with_query(&state, reqwest::Method::GET, &path, &headers, None).await
}

/// GET /api/v1/admin/tasks?limit=...&offset=...&status=...&queue_name=...&actor_name=...&search=...
#[derive(Deserialize)]
pub struct ListTasksQuery {
    pub limit: Option<i64>,
    pub offset: Option<i64>,
    pub status: Option<String>,
    pub queue_name: Option<String>,
    pub actor_name: Option<String>,
    pub search: Option<String>,
}

pub async fn list_tasks(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListTasksQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = "/api/v1/admin/tasks?".to_string();
    if let Some(l) = params.limit {
        path.push_str(&format!("limit={l}&"));
    }
    if let Some(o) = params.offset {
        path.push_str(&format!("offset={o}&"));
    }
    if let Some(ref s) = params.status {
        path.push_str(&format!("status={}&", urlencoding::encode(s)));
    }
    if let Some(ref qn) = params.queue_name {
        path.push_str(&format!("queue_name={}&", urlencoding::encode(qn)));
    }
    if let Some(ref an) = params.actor_name {
        path.push_str(&format!("actor_name={}&", urlencoding::encode(an)));
    }
    if let Some(ref search) = params.search {
        path.push_str(&format!("search={}&", urlencoding::encode(search)));
    }
    proxy_to_python_with_query(
        &state,
        reqwest::Method::GET,
        path.trim_end_matches('&'),
        &headers,
        None,
    )
    .await
}

/// GET /api/v1/admin/tasks/stream  (SSE – proxy; Python handles streaming)
#[derive(Deserialize)]
pub struct StreamTasksQuery {
    pub sample_size: Option<i64>,
    pub list_limit: Option<i64>,
    pub list_offset: Option<i64>,
    pub status: Option<String>,
    pub queue_name: Option<String>,
    pub actor_name: Option<String>,
    pub search: Option<String>,
    pub interval_ms: Option<i64>,
}

pub async fn stream_task_snapshots(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<StreamTasksQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = "/api/v1/admin/tasks/stream?".to_string();
    if let Some(ss) = params.sample_size {
        path.push_str(&format!("sample_size={ss}&"));
    }
    if let Some(ll) = params.list_limit {
        path.push_str(&format!("list_limit={ll}&"));
    }
    if let Some(lo) = params.list_offset {
        path.push_str(&format!("list_offset={lo}&"));
    }
    if let Some(ref s) = params.status {
        path.push_str(&format!("status={}&", urlencoding::encode(s)));
    }
    if let Some(ref qn) = params.queue_name {
        path.push_str(&format!("queue_name={}&", urlencoding::encode(qn)));
    }
    if let Some(ref an) = params.actor_name {
        path.push_str(&format!("actor_name={}&", urlencoding::encode(an)));
    }
    if let Some(ref search) = params.search {
        path.push_str(&format!("search={}&", urlencoding::encode(search)));
    }
    if let Some(im) = params.interval_ms {
        path.push_str(&format!("interval_ms={im}&"));
    }
    proxy_to_python_with_query(
        &state,
        reqwest::Method::GET,
        path.trim_end_matches('&'),
        &headers,
        None,
    )
    .await
}

/// POST /api/v1/admin/tasks/bulk-cancel
pub async fn bulk_cancel_tasks(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/tasks/bulk-cancel",
        &headers,
        Some(body),
    )
    .await
}

/// POST /api/v1/admin/tasks/bulk-retry
pub async fn bulk_retry_tasks(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/tasks/bulk-retry",
        &headers,
        Some(body),
    )
    .await
}

/// GET /api/v1/admin/tasks/{task_id}
pub async fn get_task_detail(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(task_id): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/tasks/{task_id}");
    proxy_to_python(&state, reqwest::Method::GET, &path, &headers, None).await
}

/// POST /api/v1/admin/tasks/{task_id}/retry
pub async fn retry_task(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(task_id): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/tasks/{task_id}/retry");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, None).await
}

/// POST /api/v1/admin/tasks/{task_id}/cancel
pub async fn cancel_task(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(task_id): Path<String>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/tasks/{task_id}/cancel");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, Some(body)).await
}

// ─────────────────────────────────────────────────────────────────────────────
// TELEGRAM  /api/v1/admin/telegram
// ─────────────────────────────────────────────────────────────────────────────

/// GET /api/v1/admin/telegram/stats
pub async fn get_telegram_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/telegram/stats",
        &headers,
        None,
    )
    .await
}

/// POST /api/v1/admin/telegram/migrate
pub async fn migrate_single_stream(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/telegram/migrate",
        &headers,
        Some(body),
    )
    .await
}

/// POST /api/v1/admin/telegram/migrate/bulk
pub async fn migrate_bulk_streams(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/telegram/migrate/bulk",
        &headers,
        Some(body),
    )
    .await
}

/// GET /api/v1/admin/telegram/exportable?limit=...&offset=...
#[derive(Deserialize)]
pub struct ExportableQuery {
    pub limit: Option<i64>,
    pub offset: Option<i64>,
}

pub async fn get_exportable_streams(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ExportableQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = "/api/v1/admin/telegram/exportable?".to_string();
    if let Some(l) = params.limit {
        path.push_str(&format!("limit={l}&"));
    }
    if let Some(o) = params.offset {
        path.push_str(&format!("offset={o}&"));
    }
    proxy_to_python_with_query(
        &state,
        reqwest::Method::GET,
        path.trim_end_matches('&'),
        &headers,
        None,
    )
    .await
}
