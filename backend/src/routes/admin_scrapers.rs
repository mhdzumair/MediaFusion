/// Admin scraper, scheduler, task, and telegram management endpoints.
/// All routes are implemented natively in Rust; they query the Postgres
/// `jobs`/`cron_jobs`/`job_events` tables directly.
///
/// Routes:
/// ── Scrapers (/api/v1/admin/scrapers) ──────────────────────────────────────
///   GET  /spiders                        → list_spiders
///   POST /run                            → run_scraper
///   POST /block-torrent                  → block_torrent
///   POST /unblock-torrent                → unblock_torrent
///   GET  /catalogs                       → get_catalog_data
///   GET  /status                         → get_scraper_status
///   GET  /imdb-dataset/status            → get_imdb_dataset_status
///   GET  /imdb-dataset/config            → get_imdb_dataset_config
///   PUT  /imdb-dataset/config            → update_imdb_dataset_config
///   POST /imdb-dataset/run               → run_imdb_dataset_import
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
///   PATCH /schedulers/{job_id}           → update_scheduler_job
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
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

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
    (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response()
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
    Json(json!({
        "spiders": [
            {"id": "prowlarr",              "name": "Prowlarr"},
            {"id": "zilean",                "name": "Zilean"},
            {"id": "dmm_hashlist",          "name": "DMM Hashlist"},
            {"id": "torrentio",             "name": "Torrentio"},
            {"id": "mediafusion",           "name": "MediaFusion"},
            {"id": "public_indexers",       "name": "Public Indexers"},
            {"id": "jackett",               "name": "Jackett"},
            {"id": "torznab",               "name": "Custom Torznab"},
            {"id": "torbox_search",         "name": "TorBox Search"},
            {"id": "newznab",               "name": "Newznab Indexers"},
            {"id": "easynews",              "name": "Easynews"},
            {"id": "public_usenet_indexers","name": "Public Usenet Indexers"}
        ]
    }))
    .into_response()
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
    let spider_name = match body["spider_name"].as_str() {
        Some(s) => s.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "spider_name is required"})),
            )
                .into_response();
        }
    };
    let Some((queue, payload)) = spider_name_to_queue(&spider_name) else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "unknown spider"})),
        )
            .into_response();
    };
    let payload = payload.unwrap_or(serde_json::json!({}));
    match crate::jobs::enqueue::enqueue_simple(&state.pool, queue, &payload, Default::default())
        .await
    {
        Ok(Some(id)) => (
            StatusCode::ACCEPTED,
            Json(json!({"status":"accepted","job_id":id})),
        )
            .into_response(),
        Ok(None) => (
            StatusCode::OK,
            Json(json!({"status":"skipped","reason":"dedupe"})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("run_scraper: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail":"enqueue failed"})),
            )
                .into_response()
        }
    }
}

fn spider_name_to_queue(spider_name: &str) -> Option<(&'static str, Option<serde_json::Value>)> {
    match spider_name {
        "tamilmv" => Some(("spider_tamilmv", None)),
        "tamil_blasters" => Some(("spider_tamil_blasters", None)),
        "formula_ext" => Some(("spider_formula_ext", None)),
        "motogp_ext" => Some(("spider_motogp_ext", None)),
        "wwe_ext" => Some(("spider_wwe_ext", None)),
        "ufc_ext" => Some(("spider_ufc_ext", None)),
        "movies_tv_ext" => Some(("spider_movies_tv_ext", None)),
        "sport_video" => Some(("spider_sport_video", None)),
        "eztv_rss" => Some(("spider_eztv_rss", None)),
        // Registry-crawl spiders — same queue, different payload
        "nyaa" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"nyaa"})),
        )),
        "animetosho" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"animetosho"})),
        )),
        "subsplease" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"subsplease"})),
        )),
        "animepahe" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"animepahe"})),
        )),
        "bt52" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"bt52"})),
        )),
        "uindex" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"uindex"})),
        )),
        "x1337" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"x1337"})),
        )),
        "thepiratebay" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"thepiratebay"})),
        )),
        "rutor" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"rutor"})),
        )),
        "limetorrents" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"limetorrents"})),
        )),
        "yts" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"yts"})),
        )),
        "bt4g" => Some((
            "spider_registry_crawl",
            Some(serde_json::json!({"indexer":"bt4g"})),
        )),
        _ => None,
    }
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
    let info_hash = match body["info_hash"].as_str() {
        Some(h) => h.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "info_hash is required"})),
            )
                .into_response();
        }
    };
    let result =
        sqlx::query("UPDATE streams SET is_blocked = true WHERE LOWER(info_hash) = LOWER($1)")
            .bind(&info_hash)
            .execute(&state.pool)
            .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Torrent not found"})),
        )
            .into_response(),
        Ok(_) => Json(json!({
            "status": "success",
            "message": "Torrent blocked",
            "info_hash": info_hash
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("block_torrent DB error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response()
        }
    }
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
    let info_hash = match body["info_hash"].as_str() {
        Some(h) => h.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "info_hash is required"})),
            )
                .into_response();
        }
    };
    let result =
        sqlx::query("UPDATE streams SET is_blocked = false WHERE LOWER(info_hash) = LOWER($1)")
            .bind(&info_hash)
            .execute(&state.pool)
            .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Torrent not found"})),
        )
            .into_response(),
        Ok(_) => Json(json!({
            "status": "success",
            "message": "Torrent unblocked",
            "info_hash": info_hash
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("unblock_torrent DB error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response()
        }
    }
}

/// GET /api/v1/admin/scrapers/catalogs
pub async fn get_catalog_data(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let fallback_languages: Vec<&str> = vec![
        "english",
        "hindi",
        "tamil",
        "telugu",
        "malayalam",
        "kannada",
        "bengali",
        "punjabi",
        "marathi",
        "gujarati",
        "odia",
        "urdu",
        "arabic",
        "spanish",
        "french",
        "german",
        "portuguese",
        "italian",
        "japanese",
        "korean",
        "chinese",
        "russian",
    ];
    let languages: Vec<String> =
        sqlx::query_scalar("SELECT DISTINCT name FROM language ORDER BY name")
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_else(|_| fallback_languages.iter().map(|s| s.to_string()).collect());
    let languages: Vec<String> = if languages.is_empty() {
        fallback_languages.iter().map(|s| s.to_string()).collect()
    } else {
        languages
    };
    Json(json!({
        "supported_movie_catalogs": ["tmdb_movies", "imdb_movies", "prowlarr_movies", "mediafusion"],
        "supported_series_catalogs": ["tmdb_series", "imdb_series", "prowlarr_series", "mediafusion"],
        "supported_languages": languages,
    }))
    .into_response()
}

/// GET /api/v1/admin/scrapers/status
pub async fn get_scraper_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    use sqlx::Row as _;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    // Query recent jobs grouped by queue with status counts
    let rows = sqlx::query(
        r#"SELECT queue,
                  COUNT(*) FILTER (WHERE status = 'pending')  AS pending,
                  COUNT(*) FILTER (WHERE status = 'running')  AS running,
                  COUNT(*) FILTER (WHERE status = 'success')  AS success,
                  COUNT(*) FILTER (WHERE status = 'error')    AS error,
                  COUNT(*) FILTER (WHERE status = 'dead')     AS dead,
                  MAX(finished_at) AS last_finished,
                  MAX(started_at)  AS last_started
           FROM jobs
           WHERE created_at > NOW() - INTERVAL '7 days'
           GROUP BY queue
           ORDER BY queue"#,
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let statuses: Vec<serde_json::Value> = rows
        .iter()
        .map(|r| {
            let last_started: Option<chrono::DateTime<chrono::Utc>> = r.get("last_started");
            let last_finished: Option<chrono::DateTime<chrono::Utc>> = r.get("last_finished");
            json!({
                "queue": r.get::<String, _>("queue"),
                "pending": r.get::<i64, _>("pending"),
                "running": r.get::<i64, _>("running"),
                "success": r.get::<i64, _>("success"),
                "error": r.get::<i64, _>("error"),
                "dead": r.get::<i64, _>("dead"),
                "last_started": last_started.map(|t| t.to_rfc3339()),
                "last_finished": last_finished.map(|t| t.to_rfc3339()),
            })
        })
        .collect();
    (StatusCode::OK, Json(json!({"scrapers": statuses}))).into_response()
}

/// GET /api/v1/admin/scrapers/dmm-hashlist/status
pub async fn get_dmm_hashlist_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    use fred::prelude::KeysInterface;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let raw: Option<String> = state.redis.get("dmm_hashlist:status").await.unwrap_or(None);

    if let Some(s) = raw {
        if let Ok(v) = serde_json::from_str::<Value>(&s) {
            return Json(v).into_response();
        }
    }

    Json(json!({
        "enabled": false,
        "scheduler_disabled": true,
        "message": "DMM hashlist status not available"
    }))
    .into_response()
}

/// GET /api/v1/admin/scrapers/imdb-dataset/status
pub async fn get_imdb_dataset_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    use fred::prelude::KeysInterface;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let raw: Option<String> = state.redis.get("imdb_import:status").await.unwrap_or(None);

    if let Some(s) = raw {
        if let Ok(v) = serde_json::from_str::<Value>(&s) {
            return Json(v).into_response();
        }
    }

    Json(json!({
        "phase": "idle",
        "message": "IMDb dataset import status not available"
    }))
    .into_response()
}

const IMDB_CRON_NAME: &str = "imdb_dataset_import";
const IMDB_DATASET_KEYS: &[&str] = &[
    "basics",
    "names",
    "ratings",
    "akas",
    "episode",
    "crew",
    "principals",
];

fn default_imdb_cron_payload() -> Value {
    json!({
        "datasets": IMDB_DATASET_KEYS,
        "include_adult": false
    })
}

fn parse_imdb_payload(payload: &Value) -> (Vec<String>, bool) {
    let datasets: Vec<String> = payload
        .get("datasets")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_ascii_lowercase))
                .filter(|k| IMDB_DATASET_KEYS.contains(&k.as_str()))
                .collect()
        })
        .filter(|v: &Vec<String>| !v.is_empty())
        .unwrap_or_else(|| IMDB_DATASET_KEYS.iter().map(|s| (*s).to_string()).collect());

    let include_adult = payload
        .get("include_adult")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    (datasets, include_adult)
}

fn merge_json_objects(base: Value, overlay: Value) -> Value {
    match (base, overlay) {
        (Value::Object(mut base_map), Value::Object(overlay_map)) => {
            for (k, v) in overlay_map {
                base_map.insert(k, v);
            }
            Value::Object(base_map)
        }
        (_, overlay) => overlay,
    }
}

/// GET /api/v1/admin/scrapers/imdb-dataset/config
pub async fn get_imdb_dataset_config(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    use sqlx::Row as _;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let cron_row = sqlx::query(
        "SELECT enabled, schedule, payload, last_enqueued_at FROM cron_jobs WHERE name = $1",
    )
    .bind(IMDB_CRON_NAME)
    .fetch_optional(&state.pool_ro)
    .await;

    let (enabled, schedule, payload, last_enqueued_at) = match cron_row {
        Ok(Some(row)) => (
            row.try_get::<bool, _>("enabled").unwrap_or(false),
            row.try_get::<String, _>("schedule")
                .unwrap_or_else(|_| "0 4 * * 0".into()),
            row.try_get::<Value, _>("payload")
                .unwrap_or_else(|_| default_imdb_cron_payload()),
            row.try_get::<Option<chrono::DateTime<chrono::Utc>>, _>("last_enqueued_at")
                .ok()
                .flatten(),
        ),
        _ => (false, "0 4 * * 0".into(), default_imdb_cron_payload(), None),
    };

    let (datasets, include_adult) = parse_imdb_payload(&payload);

    let import_state = sqlx::query(
        r#"SELECT dataset, etag, last_modified, rows_loaded, last_run_at
           FROM imdb_import_state
           ORDER BY dataset"#,
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let state_rows: Vec<Value> = import_state
        .iter()
        .filter_map(|row| {
            Some(json!({
                "dataset": row.try_get::<String, _>("dataset").ok()?,
                "etag": row.try_get::<Option<String>, _>("etag").ok()?,
                "last_modified": row.try_get::<Option<String>, _>("last_modified").ok()?,
                "rows_loaded": row.try_get::<Option<i64>, _>("rows_loaded").ok()?,
                "last_run_at": row.try_get::<Option<chrono::DateTime<chrono::Utc>>, _>("last_run_at").ok()?.map(|dt| dt.to_rfc3339()),
            }))
        })
        .collect();

    Json(json!({
        "job_id": IMDB_CRON_NAME,
        "enabled": enabled,
        "schedule": schedule,
        "datasets": datasets,
        "include_adult": include_adult,
        "base_url": state.config.imdb_datasets_base_url,
        "available_datasets": IMDB_DATASET_KEYS,
        "last_enqueued_at": last_enqueued_at.map(|dt| dt.to_rfc3339()),
        "import_state": state_rows,
    }))
    .into_response()
}

#[derive(Deserialize)]
pub struct UpdateImdbDatasetConfigRequest {
    pub enabled: Option<bool>,
    pub schedule: Option<String>,
    pub datasets: Option<Vec<String>>,
    pub include_adult: Option<bool>,
}

/// PUT /api/v1/admin/scrapers/imdb-dataset/config
pub async fn update_imdb_dataset_config(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<UpdateImdbDatasetConfigRequest>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    if let Some(ref schedule) = body.schedule {
        if !is_valid_cron_schedule(schedule) {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "schedule must be a 5-field cron expression"})),
            )
                .into_response();
        }
    }

    if let Some(ref datasets) = body.datasets {
        if datasets.is_empty() {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "datasets must not be empty"})),
            )
                .into_response();
        }
        for key in datasets {
            if !IMDB_DATASET_KEYS.contains(&key.to_ascii_lowercase().as_str()) {
                return (
                    StatusCode::UNPROCESSABLE_ENTITY,
                    Json(json!({"detail": format!("unknown dataset: {key}")})),
                )
                    .into_response();
            }
        }
    }

    let existing = sqlx::query("SELECT enabled, schedule, payload FROM cron_jobs WHERE name = $1")
        .bind(IMDB_CRON_NAME)
        .fetch_optional(&state.pool)
        .await;

    use sqlx::Row as _;

    let (current_enabled, current_schedule, mut payload) = match existing {
        Ok(Some(row)) => (
            row.try_get::<bool, _>("enabled").unwrap_or(false),
            row.try_get::<String, _>("schedule")
                .unwrap_or_else(|_| "0 4 * * 0".into()),
            row.try_get::<Value, _>("payload")
                .unwrap_or_else(|_| default_imdb_cron_payload()),
        ),
        _ => (false, "0 4 * * 0".into(), default_imdb_cron_payload()),
    };

    if let Some(datasets) = body.datasets {
        let normalized: Vec<String> = datasets.iter().map(|s| s.to_ascii_lowercase()).collect();
        if let Value::Object(ref mut map) = payload {
            map.insert("datasets".into(), json!(normalized));
        }
    }
    if let Some(include_adult) = body.include_adult {
        if let Value::Object(ref mut map) = payload {
            map.insert("include_adult".into(), json!(include_adult));
        }
    }

    let schedule = body.schedule.as_deref().unwrap_or(&current_schedule);
    let enabled = body.enabled.unwrap_or(current_enabled);

    let result = sqlx::query(
        r#"INSERT INTO cron_jobs (name, schedule, queue, payload, enabled)
           VALUES ($1, $2, 'imdb_dataset_import', $3, $4)
           ON CONFLICT (name) DO UPDATE SET
             schedule = EXCLUDED.schedule,
             payload = EXCLUDED.payload,
             enabled = EXCLUDED.enabled"#,
    )
    .bind(IMDB_CRON_NAME)
    .bind(schedule)
    .bind(&payload)
    .bind(enabled)
    .execute(&state.pool)
    .await;

    match result {
        Ok(_) => {
            let (datasets, include_adult) = parse_imdb_payload(&payload);
            Json(json!({
                "status": "updated",
                "enabled": enabled,
                "schedule": schedule,
                "datasets": datasets,
                "include_adult": include_adult,
                "payload": payload,
            }))
            .into_response()
        }
        Err(e) => {
            tracing::error!("update_imdb_dataset_config: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Failed to update IMDb import config"})),
            )
                .into_response()
        }
    }
}

#[derive(Debug, Default, Deserialize)]
pub struct RunImdbDatasetImportRequest {
    pub datasets: Option<Vec<String>>,
    #[serde(default)]
    pub force: bool,
    pub include_adult: Option<bool>,
    #[serde(default)]
    pub merge_only: bool,
}

/// POST /api/v1/admin/scrapers/imdb-dataset/run
pub async fn run_imdb_dataset_import(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<RunImdbDatasetImportRequest>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    if let Some(ref datasets) = body.datasets {
        for key in datasets {
            if !IMDB_DATASET_KEYS.contains(&key.to_ascii_lowercase().as_str()) {
                return (
                    StatusCode::UNPROCESSABLE_ENTITY,
                    Json(json!({"detail": format!("unknown dataset: {key}")})),
                )
                    .into_response();
            }
        }
    }

    let cron_payload = load_cron_payload(&state.pool_ro, IMDB_CRON_NAME)
        .await
        .unwrap_or_else(default_imdb_cron_payload);

    let mut payload = cron_payload;
    let mut overlay = serde_json::Map::new();
    if let Some(datasets) = body.datasets {
        overlay.insert(
            "datasets".into(),
            json!(datasets
                .iter()
                .map(|s| s.to_ascii_lowercase())
                .collect::<Vec<_>>()),
        );
    }
    if let Some(include_adult) = body.include_adult {
        overlay.insert("include_adult".into(), json!(include_adult));
    }
    if body.force {
        overlay.insert("force".into(), json!(true));
    }
    if body.merge_only {
        overlay.insert("merge_only".into(), json!(true));
    }
    if !overlay.is_empty() {
        payload = merge_json_objects(payload, Value::Object(overlay));
    }

    match crate::jobs::enqueue::enqueue_simple(
        &state.pool,
        "imdb_dataset_import",
        &payload,
        Default::default(),
    )
    .await
    {
        Ok(Some(id)) => (
            StatusCode::ACCEPTED,
            Json(json!({"status": "accepted", "job_id": id, "payload": payload})),
        )
            .into_response(),
        Ok(None) => (
            StatusCode::OK,
            Json(json!({"status": "skipped", "reason": "dedupe"})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("run_imdb_dataset_import: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "enqueue failed"})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/admin/scrapers/dmm-hashlist/run
pub async fn run_dmm_hashlist(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(_body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    match crate::jobs::enqueue::enqueue_simple(
        &state.pool,
        "dmm_hashlist",
        &serde_json::json!({}),
        Default::default(),
    )
    .await
    {
        Ok(Some(id)) => (
            StatusCode::ACCEPTED,
            Json(json!({"status":"accepted","job_id":id})),
        )
            .into_response(),
        Ok(None) => (
            StatusCode::OK,
            Json(json!({"status":"skipped","reason":"dedupe"})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("run_dmm_hashlist: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail":"enqueue failed"})),
            )
                .into_response()
        }
    }
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
    let mut args = body;
    if let Some(obj) = args.as_object_mut() {
        obj.insert("full".into(), json!(true));
    } else {
        args = json!({"full": true});
    }
    match crate::jobs::enqueue::enqueue_simple(
        &state.pool,
        "dmm_hashlist",
        &args,
        Default::default(),
    )
    .await
    {
        Ok(Some(id)) => (
            StatusCode::ACCEPTED,
            Json(json!({"status":"accepted","job_id":id})),
        )
            .into_response(),
        Ok(None) => (
            StatusCode::OK,
            Json(json!({"status":"skipped","reason":"dedupe"})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("run_dmm_hashlist_full: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail":"enqueue failed"})),
            )
                .into_response()
        }
    }
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

    let from_ids: Vec<i64> = match body["from_media_ids"].as_array() {
        Some(arr) => arr.iter().filter_map(|v| v.as_i64()).collect(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "from_media_ids is required"})),
            )
                .into_response();
        }
    };
    let to_id = match body["to_media_id"].as_i64() {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "to_media_id is required"})),
            )
                .into_response();
        }
    };

    if from_ids.is_empty() {
        return Json(json!({
            "status": "success",
            "message": "No source media IDs provided.",
            "from_media_ids": [],
            "migrated_sources_count": 0,
            "migrated_sources": [],
            "to_media_id": to_id,
            "stream_links_migrated": 0,
            "stream_links_deleted_as_duplicates": 0,
            "file_links_migrated": 0,
            "file_links_deleted_as_duplicates": 0
        }))
        .into_response();
    }

    let mut migrated_sources: Vec<serde_json::Value> = Vec::new();
    let mut total_stream_links: u64 = 0;

    for from_id in &from_ids {
        let res = sqlx::query(
            r#"UPDATE stream_media_link s SET media_id = $1
               WHERE media_id = $2
                 AND NOT EXISTS (
                   SELECT 1 FROM stream_media_link t
                   WHERE t.stream_id = s.stream_id AND t.media_id = $1
                 )"#,
        )
        .bind(to_id as i32)
        .bind(*from_id as i32)
        .execute(&state.pool)
        .await;
        match res {
            Ok(r) => {
                let links = r.rows_affected();
                total_stream_links += links;
                migrated_sources.push(json!({
                    "from_media_id": from_id,
                    "stream_links_migrated": links,
                    "stream_links_deleted_as_duplicates": 0,
                    "file_links_migrated": 0,
                    "file_links_deleted_as_duplicates": 0
                }));
            }
            Err(e) => {
                tracing::error!("migrate_media stream_media_link error for id {from_id}: {e}");
            }
        }
    }

    // Delete the source media rows
    let del_result = sqlx::query("DELETE FROM media WHERE id = ANY($1)")
        .bind(&from_ids)
        .execute(&state.pool)
        .await;
    if let Err(e) = del_result {
        tracing::error!("migrate_media DELETE media error: {e}");
    }

    let migrated_sources_count = migrated_sources.len();
    Json(json!({
        "status": "success",
        "message": format!("Migrated {} source(s) to media #{to_id}.", migrated_sources_count),
        "from_media_ids": from_ids,
        "migrated_sources_count": migrated_sources_count,
        "migrated_sources": migrated_sources,
        "to_media_id": to_id,
        "stream_links_migrated": total_stream_links,
        "stream_links_deleted_as_duplicates": 0,
        "file_links_migrated": 0,
        "file_links_deleted_as_duplicates": 0
    }))
    .into_response()
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

    let from_id = match body["from_id"].as_str() {
        Some(s) => s.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "from_id is required"})),
            )
                .into_response();
        }
    };
    let to_id = match body["to_id"].as_str() {
        Some(s) => s.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "to_id is required"})),
            )
                .into_response();
        }
    };

    // Check if from_id exists
    let from_exists: Option<(i64,)> =
        sqlx::query_as("SELECT media_id FROM media_external_id WHERE external_id = $1 LIMIT 1")
            .bind(&from_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    if from_exists.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("Source ID '{}' not found", from_id)})),
        )
            .into_response();
    }

    // Check if to_id already exists
    let to_exists: Option<(i64,)> =
        sqlx::query_as("SELECT media_id FROM media_external_id WHERE external_id = $1 LIMIT 1")
            .bind(&to_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    if to_exists.is_some() {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": format!("Target ID '{}' already exists; manual merge required", to_id)})),
        )
            .into_response();
    }

    // Update from_id → to_id
    let result =
        sqlx::query("UPDATE media_external_id SET external_id = $1 WHERE external_id = $2")
            .bind(&to_id)
            .bind(&from_id)
            .execute(&state.pool)
            .await;

    match result {
        Ok(r) => Json(json!({
            "status": "success",
            "message": format!("Migrated {} → {}", from_id, to_id),
            "rows_updated": r.rows_affected()
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("migrate_id error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error during ID migration"})),
            )
                .into_response()
        }
    }
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
    let meta_id = match body["meta_id"].as_str() {
        Some(s) => s.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "meta_id is required"})),
            )
                .into_response();
        }
    };
    // Find media_id
    let row: Option<(i64,)> =
        sqlx::query_as("SELECT media_id FROM media_external_id WHERE external_id = $1 LIMIT 1")
            .bind(&meta_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    let media_id = match row {
        Some((id,)) => id,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": format!("Media '{}' not found", meta_id)})),
            )
                .into_response();
        }
    };

    let image_fields = [
        ("poster", body["poster"].as_str()),
        ("background", body["background"].as_str()),
        ("logo", body["logo"].as_str()),
    ];

    let mut updated: Vec<&str> = Vec::new();
    for (image_type, url_opt) in &image_fields {
        if let Some(url) = url_opt {
            let ins = sqlx::query(
                "INSERT INTO media_image (media_id, provider_id, image_type, url, is_primary, display_order) \
                 VALUES ($1, 1, $2, $3, true, 0) ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING",
            )
            .bind(media_id)
            .bind(*image_type)
            .bind(*url)
            .execute(&state.pool)
            .await;
            if let Err(e) = ins {
                tracing::warn!("update_media_images insert {image_type} error: {e}");
            }
            let upd = sqlx::query(
                "UPDATE media_image SET url = $3 WHERE media_id = $1 AND image_type = $2 AND is_primary = true",
            )
            .bind(media_id)
            .bind(*image_type)
            .bind(*url)
            .execute(&state.pool)
            .await;
            if let Err(e) = upd {
                tracing::warn!("update_media_images update {image_type} error: {e}");
            } else {
                updated.push(image_type);
            }
        }
    }

    Json(json!({
        "status": "success",
        "meta_id": meta_id,
        "updated": updated,
    }))
    .into_response()
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

    let Some(ref tmdb_key) = state.config.tmdb_api_key else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail": "TMDB API key not configured"})),
        )
            .into_response();
    };

    let (provider, ext_id): (&str, &str) = if meta_id.starts_with("tt") {
        ("imdb", &meta_id)
    } else if meta_id.parse::<i64>().is_ok() {
        ("tmdb", &meta_id)
    } else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid meta_id: expected an IMDb tt-id or numeric TMDB id"})),
        )
            .into_response();
    };

    let is_series = params
        .media_type
        .as_deref()
        .map(|t| t.eq_ignore_ascii_case("series") || t.eq_ignore_ascii_case("show"))
        .unwrap_or(false);

    let details = match crate::scrapers::metadata::fetch_by_external_id_with_opts(
        &state.http,
        provider,
        ext_id,
        is_series,
        crate::scrapers::metadata::FetchCtx::with_tmdb_tvdb(
            Some(tmdb_key),
            state.config.tvdb_api_key.as_deref(),
            state.config.imdb_cinemeta_fallback_enabled,
        ),
    )
    .await
    {
        Some(d) => d,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Media not found at external provider"})),
            )
                .into_response()
        }
    };

    let db_media_id: Option<i64> = sqlx::query_scalar(
        r#"SELECT m.id FROM media m
           JOIN media_external_id meid ON m.id = meid.media_id
           WHERE meid.provider = $1 AND meid.external_id = $2
           LIMIT 1"#,
    )
    .bind(provider)
    .bind(ext_id)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    let Some(media_id) = db_media_id else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Media not found in database"})),
        )
            .into_response();
    };

    if let Err(e) = sqlx::query(
        "UPDATE media SET title = $1, year = $2, description = COALESCE($3, description) WHERE id = $4",
    )
    .bind(&details.title)
    .bind(details.year)
    .bind(&details.description)
    .bind(media_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("refresh_imdb_data update error: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": "Database error"})),
        )
            .into_response();
    }

    if let Some(ref poster_url) = details.poster_url {
        let _ = sqlx::query(
            r#"INSERT INTO media_image (media_id, provider_id, image_type, url, is_primary, display_order)
               VALUES ($1, 1, 'poster', $2, true, 0)
               ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING"#,
        )
        .bind(media_id)
        .bind(poster_url)
        .execute(&state.pool)
        .await;
    }

    if let Some(ref imdb_id) = details.imdb_id {
        let _ = sqlx::query(
            r#"INSERT INTO media_external_id (media_id, provider, external_id)
               VALUES ($1, 'imdb', $2)
               ON CONFLICT (media_id, provider) DO UPDATE SET external_id = EXCLUDED.external_id"#,
        )
        .bind(media_id)
        .bind(imdb_id)
        .execute(&state.pool)
        .await;
    }
    if let Some(ref tmdb_id) = details.tmdb_id {
        let _ = sqlx::query(
            r#"INSERT INTO media_external_id (media_id, provider, external_id)
               VALUES ($1, 'tmdb', $2)
               ON CONFLICT (media_id, provider) DO UPDATE SET external_id = EXCLUDED.external_id"#,
        )
        .bind(media_id)
        .bind(tmdb_id)
        .execute(&state.pool)
        .await;
    }

    Json(json!({
        "status": "success",
        "meta_id": meta_id,
        "media_id": media_id,
        "title": details.title,
        "year": details.year,
    }))
    .into_response()
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
    let _ = params.reason;
    let result = sqlx::query("DELETE FROM streams WHERE LOWER(info_hash) = LOWER($1)")
        .bind(&info_hash)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Torrent not found"})),
        )
            .into_response(),
        Ok(_) => Json(json!({
            "status": "success",
            "message": "Torrent deleted",
            "info_hash": info_hash
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("delete_torrent DB error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response()
        }
    }
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
    let title = match body["title"].as_str() {
        Some(s) => s.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "title is required"})),
            )
                .into_response();
        }
    };
    let streams_arr = match body["streams"].as_array() {
        Some(arr) => arr.clone(),
        None => vec![],
    };

    // Compute external_id as "mf_tv_" + first 12 chars of SHA-256 hex of lowercased title
    let hash_bytes = Sha256::digest(title.to_lowercase().as_bytes());
    let hash_hex: String = hash_bytes.iter().map(|b| format!("{b:02x}")).collect();
    let external_id = format!("mf_tv_{}", &hash_hex[..12]);

    // Check if already exists
    let existing: Option<(i64,)> =
        sqlx::query_as("SELECT media_id FROM media_external_id WHERE external_id = $1 LIMIT 1")
            .bind(&external_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    let media_id: i64 = if let Some((id,)) = existing {
        id
    } else {
        // Insert media row
        let row: (i64,) = match sqlx::query_as(
            "INSERT INTO media (title, catalog_type, is_public, is_blocked, created_at, updated_at, total_streams) \
             VALUES ($1, 'TV', true, false, NOW(), NOW(), 0) RETURNING id",
        )
        .bind(&title)
        .fetch_one(&state.pool)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("add_tv_metadata insert media error: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"detail": "Failed to insert media"})),
                )
                    .into_response();
            }
        };
        let new_media_id = row.0;

        // Insert external_id
        let _ = sqlx::query(
            "INSERT INTO media_external_id (media_id, provider, external_id) VALUES ($1, 'mediafusion', $2) ON CONFLICT DO NOTHING",
        )
        .bind(new_media_id)
        .bind(&external_id)
        .execute(&state.pool)
        .await;

        // Insert images
        for (image_type, url_opt) in [
            ("poster", body["poster"].as_str()),
            ("background", body["background"].as_str()),
            ("logo", body["logo"].as_str()),
        ] {
            if let Some(url) = url_opt {
                let _ = sqlx::query(
                    "INSERT INTO media_image (media_id, provider_id, image_type, url, is_primary, display_order) \
                     VALUES ($1, 1, $2, $3, true, 0) ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING",
                )
                .bind(new_media_id)
                .bind(image_type)
                .bind(url)
                .execute(&state.pool)
                .await;
            }
        }

        new_media_id
    };

    // Insert streams
    let mut streams_added = 0usize;
    for stream_obj in &streams_arr {
        let stream_name = stream_obj["name"].as_str().unwrap_or("").to_string();
        let stream_url = stream_obj["url"].as_str().unwrap_or("").to_string();
        let stream_source = stream_obj["source"].as_str().unwrap_or("").to_string();
        if stream_url.is_empty() {
            continue;
        }
        let stream_row: Result<(i64,), _> = sqlx::query_as(
            "INSERT INTO stream (stream_type, name, source, is_active, is_blocked, is_public, playback_count, created_at) \
             VALUES ('HTTP', $1, $2, true, false, true, 0, NOW()) RETURNING id",
        )
        .bind(&stream_name)
        .bind(&stream_source)
        .fetch_one(&state.pool)
        .await;

        match stream_row {
            Ok((stream_id,)) => {
                let _ = sqlx::query("INSERT INTO http_stream (stream_id, url) VALUES ($1, $2)")
                    .bind(stream_id)
                    .bind(&stream_url)
                    .execute(&state.pool)
                    .await;

                let _ = sqlx::query(
                    "INSERT INTO stream_media_link (stream_id, media_id, is_primary, is_verified, created_at) \
                     SELECT $1, $2, true, false, NOW() \
                     WHERE NOT EXISTS (SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2)",
                )
                .bind(stream_id)
                .bind(media_id)
                .execute(&state.pool)
                .await;

                streams_added += 1;
            }
            Err(e) => {
                tracing::warn!("add_tv_metadata insert stream error: {e}");
            }
        }
    }

    Json(json!({
        "status": "success",
        "media_id": media_id,
        "streams_added": streams_added,
    }))
    .into_response()
}

// ─────────────────────────────────────────────────────────────────────────────
// SCHEDULERS  /api/v1/admin/schedulers
// ─────────────────────────────────────────────────────────────────────────────

/// Static scheduler job definitions — mirrors Python's SCHEDULER_JOBS dict.
/// (id, display_name, category, description, default_crontab, is_spider)
const SCHEDULER_JOBS: &[(&str, &str, &str, &str, &str)] = &[
    // Scrapy spiders
    (
        "tamil_blasters",
        "TamilBlasters",
        "scraper",
        "Scrapes Tamil movie torrents from TamilBlasters",
        "0 */6 * * *",
    ),
    (
        "tamilmv",
        "TamilMV",
        "scraper",
        "Scrapes Tamil movie torrents from TamilMV",
        "0 */3 * * *",
    ),
    (
        "formula_ext",
        "Formula EXT",
        "scraper",
        "Scrapes Formula racing content from ext.to",
        "*/30 * * * *",
    ),
    (
        "motogp_ext",
        "MotoGP EXT",
        "scraper",
        "Scrapes MotoGP racing content from ext.to",
        "0 5 * * *",
    ),
    (
        "wwe_ext",
        "WWE EXT",
        "scraper",
        "Scrapes WWE wrestling content from ext.to",
        "10 */3 * * *",
    ),
    (
        "ufc_ext",
        "UFC EXT",
        "scraper",
        "Scrapes UFC fighting content from ext.to",
        "30 */3 * * *",
    ),
    (
        "movies_tv_ext",
        "Movies TV EXT",
        "scraper",
        "Scrapes movies and TV series from ext.to",
        "0 * * * *",
    ),
    (
        "nowmetv",
        "NowMeTV",
        "scraper",
        "Scrapes TV content from NowMeTV",
        "0 0 * * 5",
    ),
    (
        "nowsports",
        "NowSports",
        "scraper",
        "Scrapes sports content",
        "0 10 * * 5",
    ),
    (
        "tamilultra",
        "Tamil Ultra",
        "scraper",
        "Scrapes Tamil Ultra content",
        "0 8 * * 5",
    ),
    (
        "sport_video",
        "Sport Video",
        "scraper",
        "Scrapes sports video content",
        "*/20 * * * *",
    ),
    (
        "dlhd",
        "DaddyLiveHD",
        "scraper",
        "Scrapes live sports streams",
        "0 0 * * 1",
    ),
    (
        "arab_torrents",
        "Arab Torrents",
        "scraper",
        "Scrapes Arabic movie and series torrents",
        "0 0 * * *",
    ),
    (
        "x1337",
        "1337x",
        "scraper",
        "Scrapes public torrents from 1337x",
        "0 */6 * * *",
    ),
    (
        "thepiratebay",
        "The Pirate Bay",
        "scraper",
        "Scrapes public torrents from The Pirate Bay",
        "30 */6 * * *",
    ),
    (
        "rutor",
        "RuTor",
        "scraper",
        "Scrapes public torrents from RuTor",
        "45 */6 * * *",
    ),
    (
        "limetorrents",
        "LimeTorrents",
        "scraper",
        "Scrapes public torrents from LimeTorrents",
        "0 */8 * * *",
    ),
    (
        "yts",
        "YTS",
        "scraper",
        "Scrapes public movie torrents from YTS",
        "0 */12 * * *",
    ),
    (
        "bt4g",
        "BT4G RSS",
        "scraper",
        "Scrapes BT4G RSS feed",
        "15 */8 * * *",
    ),
    (
        "nyaa",
        "Nyaa",
        "scraper",
        "Scrapes anime torrents from Nyaa",
        "15 */3 * * *",
    ),
    (
        "animetosho",
        "AnimeTosho",
        "scraper",
        "Scrapes anime torrents from AnimeTosho",
        "30 */4 * * *",
    ),
    (
        "subsplease",
        "SubsPlease",
        "scraper",
        "Scrapes anime release listings from SubsPlease",
        "45 */4 * * *",
    ),
    (
        "animepahe",
        "AnimePahe",
        "scraper",
        "Scrapes hoster-style anime pages from AnimePahe",
        "0 */6 * * *",
    ),
    (
        "bt52",
        "52BT",
        "scraper",
        "Scrapes public torrents from 52BT",
        "30 */6 * * *",
    ),
    (
        "uindex",
        "UIndex",
        "scraper",
        "Scrapes public torrents from UIndex",
        "0 */4 * * *",
    ),
    (
        "eztv_rss",
        "EZTV RSS",
        "scraper",
        "Scrapes EZTV RSS feed for torrent metadata",
        "0 */2 * * *",
    ),
    (
        "dmm_hashlist_scraper",
        "DMM Hashlist Scraper",
        "scraper",
        "Ingests DMM hashlists",
        "0 * * * *",
    ),
    // Feed scrapers
    (
        "prowlarr_feed_scraper",
        "Prowlarr Feed",
        "feed",
        "Processes torrents from Prowlarr feed",
        "0 */3 * * *",
    ),
    (
        "jackett_feed_scraper",
        "Jackett Feed",
        "feed",
        "Processes torrents from Jackett feed",
        "0 */3 * * *",
    ),
    (
        "rss_feed_scraper",
        "RSS Feed",
        "feed",
        "Processes user RSS feed subscriptions",
        "0 */3 * * *",
    ),
    (
        "youtube_background_scraper",
        "YouTube Background",
        "feed",
        "Ingests YouTube trending/search results",
        "20 */6 * * *",
    ),
    (
        "acestream_background_scraper",
        "AceStream Background",
        "feed",
        "Ingests AceStream IDs from configured sources",
        "40 */6 * * *",
    ),
    (
        "telegram_background_scraper",
        "Telegram Background",
        "feed",
        "Ingests Telegram channel media in feed mode",
        "10 */6 * * *",
    ),
    // Maintenance jobs
    (
        "validate_tv_streams_in_db",
        "TV Stream Validation",
        "maintenance",
        "Validates TV streams in database",
        "0 0 * * 4",
    ),
    (
        "update_seeders",
        "Update Seeders",
        "maintenance",
        "Updates seeder counts for torrents",
        "0 0 * * 3",
    ),
    (
        "cleanup_expired_scraper_task",
        "Cleanup Scraper Tasks",
        "maintenance",
        "Cleans up expired scraper task data",
        "0 * * * *",
    ),
    (
        "cleanup_expired_cache_task",
        "Cleanup Expired Cache",
        "maintenance",
        "Cleans up expired cache entries",
        "0 0 * * *",
    ),
    (
        "background_search",
        "Background Search",
        "background",
        "Runs background searches for missing content",
        "*/3 * * * *",
    ),
    (
        "imdb_dataset_import",
        "IMDb Dataset Import",
        "background",
        "Bulk import of IMDb non-commercial metadata datasets",
        "0 4 * * 0",
    ),
];

/// Scrapy spider job IDs — use `background_tasks:run_spider:spider_name={id}` Redis key.
const SCRAPY_SPIDER_IDS: &[&str] = &[
    "formula_ext",
    "motogp_ext",
    "wwe_ext",
    "ufc_ext",
    "movies_tv_ext",
    "x1337",
    "thepiratebay",
    "rutor",
    "limetorrents",
    "yts",
    "nyaa",
    "animetosho",
    "subsplease",
    "animepahe",
    "bt4g",
    "bt52",
    "uindex",
    "eztv_rss",
    "nowmetv",
    "nowsports",
    "tamilultra",
    "sport_video",
    "tamil_blasters",
    "tamilmv",
    "dlhd",
    "arab_torrents",
];

async fn fetch_job_info(
    pool: &sqlx::PgPool,
    job_id: &str,
    display_name: &str,
    category: &str,
    description: &str,
    default_crontab: &str,
) -> Value {
    use sqlx::Row as _;

    // Map job_id → Postgres queue name
    let queue_name = job_id_to_queue(job_id);
    let cron_name = job_id_to_cron_name(job_id);

    // Query the most recent job for this queue
    let row = sqlx::query(
        r#"SELECT status, finished_at, started_at, created_at
           FROM jobs
           WHERE queue = $1
           ORDER BY created_at DESC
           LIMIT 1"#,
    )
    .bind(queue_name)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    // Query cron_jobs for schedule, payload, enabled state and last_enqueued_at
    let cron_row = if let Some(ref name) = cron_name {
        sqlx::query(
            "SELECT enabled, schedule, payload, last_enqueued_at FROM cron_jobs WHERE name = $1",
        )
        .bind(name)
        .fetch_optional(pool)
        .await
        .unwrap_or(None)
    } else {
        None
    };

    let is_enabled = cron_row
        .as_ref()
        .and_then(|r| r.try_get::<bool, _>("enabled").ok())
        .unwrap_or(true);

    let crontab = cron_row
        .as_ref()
        .and_then(|r| r.try_get::<String, _>("schedule").ok())
        .unwrap_or_else(|| default_crontab.to_string());

    let payload = cron_row
        .as_ref()
        .and_then(|r| r.try_get::<Value, _>("payload").ok())
        .unwrap_or_else(|| json!({}));

    let cron_configured = cron_row.is_some();

    let (last_run, last_run_status, is_running, time_since_last_run) = if let Some(ref r) = row {
        let status: String = r.get::<String, _>("status");
        let finished_at: Option<chrono::DateTime<chrono::Utc>> =
            r.try_get("finished_at").ok().flatten();
        let is_running_now = status == "running" || status == "pending";

        let (last_run_str, time_since) = if let Some(dt) = finished_at {
            let delta = Utc::now() - dt;
            let mins = delta.num_minutes();
            let time_since = if mins < 60 {
                format!("{mins} minute{}", if mins == 1 { "" } else { "s" })
            } else {
                let hrs = delta.num_hours();
                if hrs < 24 {
                    format!("{hrs} hour{}", if hrs == 1 { "" } else { "s" })
                } else {
                    format!(
                        "{} day{}",
                        delta.num_days(),
                        if delta.num_days() == 1 { "" } else { "s" }
                    )
                }
            };
            (Some(dt.to_rfc3339()), time_since)
        } else {
            (None, "Never completed".to_string())
        };

        (last_run_str, status, is_running_now, time_since)
    } else {
        (None, "unknown".to_string(), false, "Never run".to_string())
    };

    json!({
        "id": job_id,
        "display_name": display_name,
        "category": category,
        "description": description,
        "crontab": crontab,
        "is_enabled": is_enabled,
        "cron_configured": cron_configured,
        "payload": payload,
        "last_run": last_run,
        "last_run_status": last_run_status,
        "time_since_last_run": time_since_last_run,
        "next_run_in": null,
        "next_run_timestamp": null,
        "is_running": is_running,
    })
}

fn is_valid_cron_schedule(schedule: &str) -> bool {
    schedule.split_whitespace().count() == 5
}

fn job_id_to_cron_name(job_id: &str) -> Option<String> {
    if SCRAPY_SPIDER_IDS.contains(&job_id) {
        return match job_id {
            "tamilmv" => Some("spider_tamilmv".into()),
            "tamil_blasters" => Some("spider_tamil_blasters".into()),
            "formula_ext" => Some("spider_formula_ext".into()),
            "motogp_ext" => Some("spider_motogp_ext".into()),
            "wwe_ext" => Some("spider_wwe_ext".into()),
            "ufc_ext" => Some("spider_ufc_ext".into()),
            "movies_tv_ext" => Some("spider_movies_tv_ext".into()),
            "sport_video" => Some("spider_sport_video".into()),
            "eztv_rss" => Some("spider_eztv_rss".into()),
            "nyaa" | "animetosho" | "subsplease" | "animepahe" | "bt52" | "uindex" | "x1337"
            | "thepiratebay" | "rutor" | "limetorrents" | "yts" | "bt4g" => {
                Some(format!("spider_registry_{job_id}"))
            }
            _ => None,
        };
    }

    Some(
        match job_id {
            "dmm_hashlist_scraper" => "dmm_hashlist",
            "prowlarr_feed_scraper" => "prowlarr_feed",
            "jackett_feed_scraper" => "jackett_feed",
            "rss_feed_scraper" => "rss_feed",
            "youtube_background_scraper" => "youtube_bg",
            "acestream_background_scraper" => "acestream_bg",
            "telegram_background_scraper" => "telegram_bg",
            "validate_tv_streams_in_db" => "validate_tv",
            "cleanup_expired_scraper_task" => "cleanup_scraper_task",
            "cleanup_expired_cache_task" => "cleanup_cache",
            "imdb_dataset_import" => "imdb_dataset_import",
            id if job_id_to_queue(id) != "default" => id,
            _ => return None,
        }
        .to_string(),
    )
}

async fn load_cron_payload(pool: &sqlx::PgPool, cron_name: &str) -> Option<Value> {
    use sqlx::Row as _;

    sqlx::query("SELECT payload FROM cron_jobs WHERE name = $1")
        .bind(cron_name)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
        .and_then(|row| row.try_get::<Value, _>("payload").ok())
}

async fn load_dispatch_payload(pool: &sqlx::PgPool, job_id: &str, default_payload: Value) -> Value {
    if let Some(cron_name) = job_id_to_cron_name(job_id) {
        if let Some(stored) = load_cron_payload(pool, &cron_name).await {
            return stored;
        }
    }
    default_payload
}

/// Map a scheduler job_id to its Postgres queue name.
fn job_id_to_queue(job_id: &str) -> &'static str {
    // Spider IDs map via spider_name_to_queue which gives the worker queue
    if SCRAPY_SPIDER_IDS.contains(&job_id) {
        // For status display, use the spider queue (generic mapping)
        return match job_id {
            "tamilmv" | "tamil_blasters" => "spider_tamilmv",
            "formula_ext" => "spider_formula_ext",
            "motogp_ext" => "spider_motogp_ext",
            "wwe_ext" => "spider_wwe_ext",
            "ufc_ext" => "spider_ufc_ext",
            "movies_tv_ext" => "spider_movies_tv_ext",
            "sport_video" => "spider_sport_video",
            "eztv_rss" => "spider_eztv_rss",
            _ => "spider_registry_crawl",
        };
    }
    match job_id {
        "dmm_hashlist_scraper" => "dmm_hashlist",
        "prowlarr_feed_scraper" => "prowlarr_feed",
        "jackett_feed_scraper" => "jackett_feed",
        "rss_feed_scraper" => "rss_feed",
        "youtube_background_scraper" => "youtube_bg",
        "acestream_background_scraper" => "acestream_bg",
        "telegram_background_scraper" => "telegram_bg",
        "validate_tv_streams_in_db" => "validate_tv",
        "update_seeders" => "update_seeders",
        "cleanup_expired_scraper_task" => "cleanup",
        "cleanup_expired_cache_task" => "cleanup",
        "background_search" => "background_search",
        "imdb_dataset_import" => "imdb_dataset_import",
        _ => "default",
    }
}

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

    let futures: Vec<_> = SCHEDULER_JOBS
        .iter()
        .filter(|(_id, _, cat, _, _)| {
            params.category.as_deref().is_none_or(|c| c == *cat)
                && !params.enabled_only.unwrap_or(false)
                || params.enabled_only == Some(true)
        })
        .map(|(id, name, cat, desc, cron)| {
            fetch_job_info(&state.pool_ro, id, name, cat, desc, cron)
        })
        .collect();

    let jobs: Vec<Value> = futures::future::join_all(futures).await;

    // Filter by category after fetch (simpler than pre-filter since join_all needs sized iter)
    let jobs: Vec<Value> = if let Some(ref cat) = params.category {
        jobs.into_iter()
            .filter(|j| j["category"].as_str() == Some(cat.as_str()))
            .collect()
    } else {
        jobs
    };

    let total = jobs.len();
    let active = jobs
        .iter()
        .filter(|j| j["is_enabled"].as_bool() == Some(true))
        .count();
    let running = jobs
        .iter()
        .filter(|j| j["is_running"].as_bool() == Some(true))
        .count();

    Json(json!({
        "jobs": jobs,
        "total": total,
        "active": active,
        "disabled": total - active,
        "running": running,
        "global_scheduler_disabled": false,
    }))
    .into_response()
}

/// GET /api/v1/admin/schedulers/stats
pub async fn get_scheduler_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    // Build stats from the same Postgres data
    let futures: Vec<_> = SCHEDULER_JOBS
        .iter()
        .map(|(id, name, cat, desc, cron)| {
            fetch_job_info(&state.pool_ro, id, name, cat, desc, cron)
        })
        .collect();
    let jobs: Vec<Value> = futures::future::join_all(futures).await;

    let total = jobs.len();
    let running = jobs
        .iter()
        .filter(|j| j["is_running"].as_bool() == Some(true))
        .count();
    let mut by_category: std::collections::HashMap<&str, usize> = std::collections::HashMap::new();
    for j in &jobs {
        if let Some(cat) = j["category"].as_str() {
            *by_category.entry(cat).or_insert(0) += 1;
        }
    }

    Json(json!({
        "total_jobs": total,
        "active_jobs": total,
        "disabled_jobs": 0,
        "running_jobs": running,
        "jobs_by_category": by_category,
        "global_scheduler_disabled": false,
    }))
    .into_response()
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
    if let Some(&(id, name, cat, desc, cron)) = SCHEDULER_JOBS
        .iter()
        .find(|(id, ..)| *id == job_id.as_str())
    {
        let info = fetch_job_info(&state.pool_ro, id, name, cat, desc, cron).await;
        Json(info).into_response()
    } else {
        (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Job not found"})),
        )
            .into_response()
    }
}

#[derive(Deserialize)]
pub struct UpdateSchedulerJobRequest {
    pub enabled: Option<bool>,
    pub schedule: Option<String>,
    pub payload: Option<Value>,
}

/// PATCH /api/v1/admin/schedulers/{job_id}
pub async fn update_scheduler_job(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    Json(body): Json<UpdateSchedulerJobRequest>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let Some((_, _name, _cat, _desc, default_schedule)) = SCHEDULER_JOBS
        .iter()
        .find(|(id, ..)| *id == job_id.as_str())
    else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Job not found"})),
        )
            .into_response();
    };

    let Some(cron_name) = job_id_to_cron_name(&job_id) else {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"detail": "Job is not backed by a cron_jobs row"})),
        )
            .into_response();
    };

    if let Some(ref schedule) = body.schedule {
        if !is_valid_cron_schedule(schedule) {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "schedule must be a 5-field cron expression"})),
            )
                .into_response();
        }
    }

    use sqlx::Row as _;

    let existing =
        sqlx::query("SELECT enabled, schedule, payload, queue FROM cron_jobs WHERE name = $1")
            .bind(&cron_name)
            .fetch_optional(&state.pool)
            .await;

    let queue_name = job_id_to_queue(&job_id);
    let (current_enabled, current_schedule, current_payload) = match existing {
        Ok(Some(row)) => (
            row.try_get::<bool, _>("enabled").unwrap_or(true),
            row.try_get::<String, _>("schedule")
                .unwrap_or_else(|_| default_schedule.to_string()),
            row.try_get::<Value, _>("payload")
                .unwrap_or_else(|_| json!({})),
        ),
        _ => (true, default_schedule.to_string(), json!({})),
    };

    let enabled = body.enabled.unwrap_or(current_enabled);
    let schedule = body
        .schedule
        .as_deref()
        .unwrap_or(current_schedule.as_str());
    let payload = body
        .payload
        .map(|p| merge_json_objects(current_payload.clone(), p))
        .unwrap_or(current_payload);

    let result = sqlx::query(
        r#"INSERT INTO cron_jobs (name, schedule, queue, payload, enabled)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (name) DO UPDATE SET
             schedule = EXCLUDED.schedule,
             payload = EXCLUDED.payload,
             enabled = EXCLUDED.enabled"#,
    )
    .bind(&cron_name)
    .bind(schedule)
    .bind(queue_name)
    .bind(&payload)
    .bind(enabled)
    .execute(&state.pool)
    .await;

    match result {
        Ok(_) => {
            let info = fetch_job_info(
                &state.pool_ro,
                &job_id,
                _name,
                _cat,
                _desc,
                default_schedule,
            )
            .await;
            Json(info).into_response()
        }
        Err(e) => {
            tracing::error!("update_scheduler_job: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Failed to update scheduler job"})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/admin/schedulers/{job_id}/run?force_run=...
#[derive(Deserialize)]
pub struct RunSchedulerQuery {
    pub force_run: Option<bool>,
}

async fn dispatch_scheduler_job(
    pool: &sqlx::PgPool,
    job_id: &str,
    force_run: Option<bool>,
    run_payload: Option<Value>,
) -> Result<i64, ()> {
    // Verify job_id is known
    if !SCHEDULER_JOBS.iter().any(|(id, ..)| *id == job_id) {
        return Err(());
    }

    let (queue_name, default_payload): (&str, serde_json::Value) =
        if SCRAPY_SPIDER_IDS.contains(&job_id) {
            // Use spider_name_to_queue to get the correct queue+payload
            match spider_name_to_queue(job_id) {
                Some((q, Some(p))) => (q, p),
                Some((q, None)) => (q, json!({})),
                None => return Err(()),
            }
        } else {
            match job_id {
                "dmm_hashlist_scraper" => ("dmm_hashlist", json!({})),
                "prowlarr_feed_scraper" => ("prowlarr_feed", json!({})),
                "jackett_feed_scraper" => ("jackett_feed", json!({})),
                "rss_feed_scraper" => ("rss_feed", json!({})),
                "youtube_background_scraper" => ("youtube_bg", json!({})),
                "acestream_background_scraper" => ("acestream_bg", json!({})),
                "telegram_background_scraper" => ("telegram_bg", json!({})),
                "validate_tv_streams_in_db" => ("validate_tv", json!({})),
                "update_seeders" => ("update_seeders", json!({})),
                "cleanup_expired_scraper_task" => ("cleanup", json!({"task": "scraper_task"})),
                "cleanup_expired_cache_task" => ("cleanup", json!({"task": "cache"})),
                "background_search" => ("background_search", json!({})),
                "imdb_dataset_import" => ("imdb_dataset_import", json!({})),
                _ => return Err(()),
            }
        };

    let mut payload = load_dispatch_payload(pool, job_id, default_payload).await;
    if let Some(overlay) = run_payload {
        payload = merge_json_objects(payload, overlay);
    }
    if force_run.unwrap_or(false) && job_id == "imdb_dataset_import" {
        payload = merge_json_objects(payload, json!({"force": true}));
    }

    crate::jobs::enqueue::enqueue_simple(pool, queue_name, &payload, Default::default())
        .await
        .map_err(|e| {
            tracing::error!("dispatch_scheduler_job enqueue error for {job_id}: {e}");
        })
        .map(|opt| opt.unwrap_or(0))
}

pub async fn run_scheduler_job(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    Query(params): Query<RunSchedulerQuery>,
    body: Option<Json<Value>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    match dispatch_scheduler_job(
        &state.pool,
        &job_id,
        params.force_run,
        body.map(|Json(v)| v),
    )
    .await
    {
        Ok(job_id_pg) => (
            StatusCode::ACCEPTED,
            Json(json!({"status": "accepted", "job_id": job_id_pg, "scheduler_job_id": job_id})),
        )
            .into_response(),
        Err(_) => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("Job '{}' not found", job_id)})),
        )
            .into_response(),
    }
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
    match dispatch_scheduler_job(&state.pool, &job_id, None, None).await {
        Ok(job_id_pg) => (
            StatusCode::ACCEPTED,
            Json(json!({"status": "accepted", "job_id": job_id_pg, "scheduler_job_id": job_id})),
        )
            .into_response(),
        Err(_) => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("Job '{}' not found", job_id)})),
        )
            .into_response(),
    }
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
    use sqlx::Row as _;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let limit = params.limit.unwrap_or(50).clamp(1, 200);
    let queue_name = job_id_to_queue(&job_id);

    let rows = sqlx::query(
        r#"SELECT id, status, created_at, started_at, finished_at, last_error, attempts
           FROM jobs
           WHERE queue = $1
           ORDER BY created_at DESC
           LIMIT $2"#,
    )
    .bind(queue_name)
    .bind(limit)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let entries: Vec<serde_json::Value> = rows
        .iter()
        .map(|r| {
            let created_at: Option<chrono::DateTime<chrono::Utc>> =
                r.try_get("created_at").ok().flatten();
            let started_at: Option<chrono::DateTime<chrono::Utc>> =
                r.try_get("started_at").ok().flatten();
            let finished_at: Option<chrono::DateTime<chrono::Utc>> =
                r.try_get("finished_at").ok().flatten();
            json!({
                "job_id": r.get::<i64, _>("id"),
                "status": r.get::<String, _>("status"),
                "created_at": created_at.map(|t| t.to_rfc3339()),
                "started_at": started_at.map(|t| t.to_rfc3339()),
                "finished_at": finished_at.map(|t| t.to_rfc3339()),
                "error": r.try_get::<Option<String>, _>("last_error").ok().flatten(),
                "attempts": r.get::<i32, _>("attempts"),
            })
        })
        .collect();

    let total = entries.len();
    let display_name = SCHEDULER_JOBS
        .iter()
        .find(|(id, ..)| *id == job_id.as_str())
        .map(|(_, name, ..)| *name)
        .unwrap_or("");
    Json(
        json!({"job_id": job_id, "display_name": display_name, "entries": entries, "total": total}),
    )
    .into_response()
}

// ─────────────────────────────────────────────────────────────────────────────
// TASKS  /api/v1/admin/tasks
// ─────────────────────────────────────────────────────────────────────────────

// ─── Task Postgres helpers ────────────────────────────────────────────────────

async fn load_tasks(pool: &sqlx::PgPool, limit: i64) -> Vec<Value> {
    use sqlx::Row as _;

    let rows = sqlx::query(
        r#"SELECT id, queue, payload, status, attempts, last_error,
                  created_at, started_at, finished_at, worker_id
           FROM jobs
           ORDER BY created_at DESC
           LIMIT $1"#,
    )
    .bind(limit)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    rows.iter()
        .map(|r| {
            let id: i64 = r.get("id");
            let status: String = r.get("status");
            let created_at: Option<chrono::DateTime<chrono::Utc>> =
                r.try_get("created_at").ok().flatten();
            let started_at: Option<chrono::DateTime<chrono::Utc>> =
                r.try_get("started_at").ok().flatten();
            let finished_at: Option<chrono::DateTime<chrono::Utc>> =
                r.try_get("finished_at").ok().flatten();
            let queue: String = r.get("queue");
            let is_running = status == "running";
            json!({
                "task_id": id.to_string(),
                "actor_name": queue,
                "queue_name": queue,
                "status": status,
                "is_running": is_running,
                "attempts": r.get::<i32, _>("attempts"),
                "error": r.try_get::<Option<String>, _>("last_error").ok().flatten(),
                "worker_id": r.try_get::<Option<String>, _>("worker_id").ok().flatten(),
                "created_at": created_at.map(|t| t.to_rfc3339()),
                "started_at": started_at.map(|t| t.to_rfc3339()),
                "finished_at": finished_at.map(|t| t.to_rfc3339()),
            })
        })
        .collect()
}

fn matches_task_filter(
    rec: &Value,
    status: Option<&str>,
    queue_name: Option<&str>,
    actor_name: Option<&str>,
    search: Option<&str>,
) -> bool {
    if let Some(s) = status {
        if rec["status"].as_str().unwrap_or("") != s {
            return false;
        }
    }
    if let Some(q) = queue_name {
        if rec["queue_name"].as_str().unwrap_or("") != q {
            return false;
        }
    }
    if let Some(a) = actor_name {
        if rec["actor_name"].as_str().unwrap_or("") != a {
            return false;
        }
    }
    if let Some(needle) = search {
        let needle = needle.to_lowercase();
        let haystack = format!(
            "{} {} {} {} {}",
            rec["task_id"].as_str().unwrap_or(""),
            rec["actor_name"].as_str().unwrap_or(""),
            rec["queue_name"].as_str().unwrap_or(""),
            rec["status"].as_str().unwrap_or(""),
            rec["error"].as_str().unwrap_or(""),
        )
        .to_lowercase();
        if !haystack.contains(&needle) {
            return false;
        }
    }
    true
}

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
    use sqlx::Row as _;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let sample = params.sample_size.unwrap_or(200).clamp(1, 2000);
    let records = load_tasks(&state.pool_ro, sample).await;

    // Query status counts for the last 7 days directly
    let stat_rows = sqlx::query(
        "SELECT status, COUNT(*) as cnt FROM jobs WHERE created_at > now() - interval '7 days' GROUP BY status",
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut global_status_counts: std::collections::HashMap<String, i64> =
        std::collections::HashMap::new();
    for r in &stat_rows {
        let status: String = r.get("status");
        let cnt: i64 = r.get("cnt");
        global_status_counts.insert(status, cnt);
    }

    let running_task_ids: Vec<String> = records
        .iter()
        .filter(|r| r["status"].as_str() == Some("running"))
        .filter_map(|r| r["task_id"].as_str().map(|s| s.to_string()))
        .collect();

    let total = records.len();
    let mut queue_counts: std::collections::HashMap<
        String,
        std::collections::HashMap<String, usize>,
    > = std::collections::HashMap::new();
    for rec in &records {
        let status = rec["status"].as_str().unwrap_or("unknown").to_string();
        let queue = rec["queue_name"].as_str().unwrap_or("default").to_string();
        *queue_counts
            .entry(queue)
            .or_default()
            .entry(status)
            .or_insert(0) += 1;
    }
    let queue_summaries: Vec<Value> = queue_counts
        .into_iter()
        .map(|(q, counts)| {
            let total_q: usize = counts.values().sum();
            let currently_running = counts.get("running").copied().unwrap_or(0);
            json!({"queue_name": q.clone(), "stream_name": q, "recent_total": total_q, "status_counts": counts, "currently_running": currently_running})
        })
        .collect();

    Json(json!({
        "timestamp": Utc::now().to_rfc3339(),
        "total_recent_tasks": total,
        "running_task_ids": running_task_ids,
        "queue_summaries": queue_summaries,
        "global_status_counts": global_status_counts,
    }))
    .into_response()
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
    let offset = params.offset.unwrap_or(0) as usize;
    let limit = params.limit.unwrap_or(25).clamp(1, 200) as usize;
    let fetch_size = (limit * 5 + offset).clamp(200, 2000) as i64;

    let all_records = load_tasks(&state.pool_ro, fetch_size).await;

    let filtered: Vec<&Value> = all_records
        .iter()
        .filter(|r| {
            matches_task_filter(
                r,
                params.status.as_deref(),
                params.queue_name.as_deref(),
                params.actor_name.as_deref(),
                params.search.as_deref(),
            )
        })
        .collect();

    let total = filtered.len();
    let mut status_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    for rec in &filtered {
        let s = rec["status"].as_str().unwrap_or("unknown").to_string();
        *status_counts.entry(s).or_insert(0) += 1;
    }
    let running_task_ids: Vec<String> = all_records
        .iter()
        .filter(|r| r["status"].as_str() == Some("running"))
        .filter_map(|r| r["task_id"].as_str().map(|s| s.to_string()))
        .collect();
    let page: Vec<Value> = filtered
        .into_iter()
        .skip(offset)
        .take(limit)
        .cloned()
        .collect();

    Json(json!({
        "tasks": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "status_counts": status_counts,
        "running_task_ids": running_task_ids,
    }))
    .into_response()
}

/// GET /api/v1/admin/tasks/stream — SSE stream of task snapshots
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
    let sample = params.sample_size.unwrap_or(200).clamp(1, 2000);
    let list_offset = params.list_offset.unwrap_or(0) as usize;
    let list_limit = params.list_limit.unwrap_or(25).clamp(1, 200) as usize;

    let all_records = load_tasks(&state.pool_ro, sample).await;

    let filtered: Vec<&Value> = all_records
        .iter()
        .filter(|r| {
            matches_task_filter(
                r,
                params.status.as_deref(),
                params.queue_name.as_deref(),
                params.actor_name.as_deref(),
                params.search.as_deref(),
            )
        })
        .collect();
    let total = filtered.len();
    let mut status_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    for rec in &filtered {
        *status_counts
            .entry(rec["status"].as_str().unwrap_or("unknown").to_string())
            .or_insert(0) += 1;
    }
    let running_task_ids: Vec<String> = all_records
        .iter()
        .filter(|r| r["status"].as_str() == Some("running"))
        .filter_map(|r| r["task_id"].as_str().map(|s| s.to_string()))
        .collect();
    let page: Vec<Value> = filtered
        .into_iter()
        .skip(list_offset)
        .take(list_limit)
        .cloned()
        .collect();

    let snapshot = json!({
        "timestamp": Utc::now().to_rfc3339(),
        "overview": {
            "total_recent_tasks": all_records.len(),
            "running_task_ids": &running_task_ids,
            "queue_summaries": [],
            "global_status_counts": &status_counts,
        },
        "list": {
            "tasks": page,
            "total": total,
            "offset": list_offset,
            "limit": list_limit,
            "status_counts": &status_counts,
            "running_task_ids": &running_task_ids,
        }
    });
    let body = format!("event: snapshot\ndata: {}\n\n", snapshot);
    axum::response::Response::builder()
        .status(axum::http::StatusCode::OK)
        .header("content-type", "text/event-stream")
        .header("cache-control", "no-cache")
        .body(axum::body::Body::from(body))
        .unwrap_or_else(|_| axum::http::StatusCode::INTERNAL_SERVER_ERROR.into_response())
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

    let limit = body["limit"].as_i64().unwrap_or(100) as usize;

    // Determine which job IDs to cancel
    let job_ids: Vec<i64> = if let Some(arr) = body["task_ids"].as_array() {
        arr.iter()
            .filter_map(|v| {
                v.as_str()
                    .and_then(|s| s.parse::<i64>().ok())
                    .or_else(|| v.as_i64())
            })
            .collect()
    } else {
        // Filter from recent tasks
        let fetch_size = (limit * 5).clamp(200, 2000) as i64;
        let records = load_tasks(&state.pool_ro, fetch_size).await;
        records
            .iter()
            .filter(|r| {
                matches_task_filter(
                    r,
                    body["status"].as_str(),
                    body["queue_name"].as_str(),
                    body["actor_name"].as_str(),
                    body["search"].as_str(),
                )
            })
            .take(limit)
            .filter_map(|r| r["task_id"].as_str().and_then(|s| s.parse::<i64>().ok()))
            .collect()
    };

    let mut cancelled_ids: Vec<i64> = Vec::new();
    for job_id in &job_ids {
        let result = sqlx::query(
            "UPDATE jobs SET cancel_requested = true WHERE id = $1 AND status = 'running'",
        )
        .bind(job_id)
        .execute(&state.pool)
        .await;
        if result.is_ok() {
            cancelled_ids.push(*job_id);
        }
    }

    Json(json!({"cancelled": cancelled_ids.len(), "task_ids": cancelled_ids})).into_response()
}

/// POST /api/v1/admin/tasks/bulk-retry
pub async fn bulk_retry_tasks(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    use sqlx::Row as _;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let limit = body["limit"].as_i64().unwrap_or(100) as usize;

    let job_ids: Vec<i64> = if let Some(arr) = body["task_ids"].as_array() {
        arr.iter()
            .filter_map(|v| {
                v.as_str()
                    .and_then(|s| s.parse::<i64>().ok())
                    .or_else(|| v.as_i64())
            })
            .collect()
    } else {
        let fetch_size = (limit * 5).clamp(200, 2000) as i64;
        let records = load_tasks(&state.pool_ro, fetch_size).await;
        records
            .iter()
            .filter(|r| {
                matches_task_filter(
                    r,
                    body["status"].as_str(),
                    body["queue_name"].as_str(),
                    body["actor_name"].as_str(),
                    body["search"].as_str(),
                )
            })
            .take(limit)
            .filter_map(|r| r["task_id"].as_str().and_then(|s| s.parse::<i64>().ok()))
            .collect()
    };

    let mut retried = 0usize;
    let mut failed = 0usize;
    let mut new_job_ids: Vec<i64> = Vec::new();

    for job_id in &job_ids {
        // Look up the original job's queue and payload
        let row = sqlx::query("SELECT queue, payload FROM jobs WHERE id = $1")
            .bind(job_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

        match row {
            Some(r) => {
                let queue: String = r.get("queue");
                let payload: serde_json::Value = r.get("payload");
                match crate::jobs::enqueue::enqueue_simple(
                    &state.pool,
                    &queue,
                    &payload,
                    Default::default(),
                )
                .await
                {
                    Ok(Some(new_id)) => {
                        new_job_ids.push(new_id);
                        retried += 1;
                    }
                    Ok(None) => {
                        // Dedupe skipped — still count as retried
                        retried += 1;
                    }
                    Err(e) => {
                        tracing::warn!("bulk_retry_tasks enqueue error for job {job_id}: {e}");
                        failed += 1;
                    }
                }
            }
            None => {
                failed += 1;
            }
        }
    }

    Json(json!({"retried": retried, "failed": failed, "new_job_ids": new_job_ids})).into_response()
}

/// GET /api/v1/admin/tasks/{task_id}
pub async fn get_task_detail(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(task_id): Path<String>,
) -> impl IntoResponse {
    use sqlx::Row as _;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let job_id: i64 = match task_id.parse() {
        Ok(id) => id,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "task_id must be an integer"})),
            )
                .into_response();
        }
    };

    let row = sqlx::query(
        r#"SELECT id, queue, payload, status, attempts, last_error,
                  created_at, started_at, finished_at, worker_id, cancel_requested
           FROM jobs WHERE id = $1"#,
    )
    .bind(job_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let row = match row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Task not found"})),
            )
                .into_response();
        }
    };

    let created_at: Option<chrono::DateTime<chrono::Utc>> =
        row.try_get("created_at").ok().flatten();
    let started_at: Option<chrono::DateTime<chrono::Utc>> =
        row.try_get("started_at").ok().flatten();
    let finished_at: Option<chrono::DateTime<chrono::Utc>> =
        row.try_get("finished_at").ok().flatten();
    let status: String = row.get("status");

    // Fetch job events
    let event_rows =
        sqlx::query("SELECT event, detail, at FROM job_events WHERE job_id = $1 ORDER BY at DESC")
            .bind(job_id)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default();

    let events: Vec<Value> = event_rows
        .iter()
        .map(|r| {
            let at: Option<chrono::DateTime<chrono::Utc>> = r.try_get("at").ok().flatten();
            json!({
                "event": r.get::<String, _>("event"),
                "detail": r.try_get::<Option<String>, _>("detail").ok().flatten(),
                "at": at.map(|t| t.to_rfc3339()),
            })
        })
        .collect();

    Json(json!({
        "task_id": job_id.to_string(),
        "actor_name": row.get::<String, _>("queue"),
        "queue_name": row.get::<String, _>("queue"),
        "payload": row.get::<serde_json::Value, _>("payload"),
        "status": status,
        "is_running": status == "running",
        "attempts": row.get::<i32, _>("attempts"),
        "error": row.try_get::<Option<String>, _>("last_error").ok().flatten(),
        "worker_id": row.try_get::<Option<String>, _>("worker_id").ok().flatten(),
        "cancel_requested": row.try_get::<bool, _>("cancel_requested").ok().unwrap_or(false),
        "created_at": created_at.map(|t| t.to_rfc3339()),
        "started_at": started_at.map(|t| t.to_rfc3339()),
        "finished_at": finished_at.map(|t| t.to_rfc3339()),
        "events": events,
    }))
    .into_response()
}

/// POST /api/v1/admin/tasks/{task_id}/retry
pub async fn retry_task(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(task_id): Path<String>,
) -> impl IntoResponse {
    use sqlx::Row as _;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let job_id: i64 = match task_id.parse() {
        Ok(id) => id,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "task_id must be an integer"})),
            )
                .into_response();
        }
    };

    let row = sqlx::query("SELECT queue, payload FROM jobs WHERE id = $1")
        .bind(job_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    let row = match row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Task not found"})),
            )
                .into_response();
        }
    };

    let queue: String = row.get("queue");
    let payload: serde_json::Value = row.get("payload");

    match crate::jobs::enqueue::enqueue_simple(&state.pool, &queue, &payload, Default::default())
        .await
    {
        Ok(Some(new_id)) => (
            StatusCode::ACCEPTED,
            Json(json!({
                "status": "accepted",
                "new_task_id": new_id.to_string(),
                "original_task_id": task_id,
            })),
        )
            .into_response(),
        Ok(None) => (
            StatusCode::OK,
            Json(json!({
                "status": "skipped",
                "reason": "dedupe",
                "original_task_id": task_id,
            })),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("retry_task enqueue error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Failed to enqueue task"})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/admin/tasks/{task_id}/cancel
pub async fn cancel_task(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(task_id): Path<String>,
    Json(_body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let job_id: i64 = match task_id.parse() {
        Ok(id) => id,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "task_id must be an integer"})),
            )
                .into_response();
        }
    };

    // Check the job exists
    let exists: Option<(i64,)> = sqlx::query_as("SELECT id FROM jobs WHERE id = $1")
        .bind(job_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    if exists.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Task not found"})),
        )
            .into_response();
    }

    let result =
        sqlx::query("UPDATE jobs SET cancel_requested = true WHERE id = $1 AND status = 'running'")
            .bind(job_id)
            .execute(&state.pool)
            .await;

    match result {
        Ok(_) => (
            StatusCode::ACCEPTED,
            Json(json!({"status": "accepted", "task_id": task_id})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("cancel_task DB error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response()
        }
    }
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
    let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM telegram_stream")
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);
    Json(json!({"total_streams": total})).into_response()
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
    let file_unique_id = match body["file_unique_id"].as_str() {
        Some(s) => s.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "file_unique_id is required"})),
            )
                .into_response();
        }
    };
    let new_file_id = match body["new_file_id"].as_str() {
        Some(s) => s.to_string(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "new_file_id is required"})),
            )
                .into_response();
        }
    };

    let result = sqlx::query("UPDATE telegram_stream SET file_id = $1 WHERE file_unique_id = $2")
        .bind(&new_file_id)
        .bind(&file_unique_id)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response(),
        Ok(r) => Json(json!({"status": "success", "updated": r.rows_affected()})).into_response(),
        Err(e) => {
            tracing::error!("migrate_single_stream DB error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response()
        }
    }
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
    let migrations = match body["migrations"].as_array() {
        Some(arr) => arr.clone(),
        None => {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail": "migrations array is required"})),
            )
                .into_response();
        }
    };
    let total = migrations.len();
    let mut updated = 0u64;
    let mut not_found = 0usize;

    for item in &migrations {
        let file_unique_id = match item["file_unique_id"].as_str() {
            Some(s) => s.to_string(),
            None => {
                not_found += 1;
                continue;
            }
        };
        let new_file_id = match item["new_file_id"].as_str() {
            Some(s) => s.to_string(),
            None => {
                not_found += 1;
                continue;
            }
        };
        match sqlx::query("UPDATE telegram_stream SET file_id = $1 WHERE file_unique_id = $2")
            .bind(&new_file_id)
            .bind(&file_unique_id)
            .execute(&state.pool)
            .await
        {
            Ok(r) if r.rows_affected() == 0 => {
                not_found += 1;
            }
            Ok(r) => {
                updated += r.rows_affected();
            }
            Err(e) => {
                tracing::warn!("migrate_bulk_streams error for {file_unique_id}: {e}");
                not_found += 1;
            }
        }
    }

    Json(json!({
        "status": "success",
        "total": total,
        "updated": updated,
        "not_found": not_found,
    }))
    .into_response()
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
    let limit = params.limit.unwrap_or(50).clamp(1, 500);
    let offset = params.offset.unwrap_or(0).max(0);

    type StreamRow = (
        i64,
        Option<i64>,
        Option<i64>,
        Option<String>,
        Option<String>,
    );
    let rows: Vec<StreamRow> = sqlx::query_as(
        "SELECT id, chat_id, message_id, file_unique_id, title FROM telegram_stream \
         WHERE file_unique_id IS NOT NULL \
         ORDER BY id DESC \
         LIMIT $1 OFFSET $2",
    )
    .bind(limit)
    .bind(offset)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let total: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM telegram_stream WHERE file_unique_id IS NOT NULL")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

    let streams: Vec<Value> = rows
        .into_iter()
        .map(|(id, chat_id, message_id, file_unique_id, title)| {
            json!({"id": id, "chat_id": chat_id, "message_id": message_id, "file_unique_id": file_unique_id, "title": title})
        })
        .collect();

    Json(json!({"streams": streams, "total": total})).into_response()
}
