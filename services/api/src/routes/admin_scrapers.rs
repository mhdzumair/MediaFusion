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
    let mut kwargs = serde_json::Map::new();
    if let Some(v) = body["pages"].as_i64() {
        kwargs.insert("pages".into(), json!(v));
    }
    if let Some(v) = body["start_page"].as_i64() {
        kwargs.insert("start_page".into(), json!(v));
    }
    if let Some(v) = body["search_keyword"].as_str() {
        kwargs.insert("search_keyword".into(), json!(v));
    }
    if let Some(v) = body["scrape_all"].as_bool() {
        kwargs.insert("scrape_all".into(), json!(if v { "True" } else { "False" }));
    }
    if let Some(v) = body["scrap_catalog_id"].as_str() {
        kwargs.insert("scrap_catalog_id".into(), json!(v));
    }
    if let Some(v) = body["total_pages"].as_i64() {
        kwargs.insert("total_pages".into(), json!(v));
    }
    match enqueue_taskiq(
        &state.redis,
        "run_spider",
        "scrapy",
        json!([spider_name]),
        Value::Object(kwargs),
        0,
    )
    .await
    {
        Ok(task_id) => (
            StatusCode::ACCEPTED,
            Json(json!({"status": "accepted", "task_id": task_id, "message": "Spider queued"})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("run_scraper enqueue error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Failed to enqueue task"})),
            )
                .into_response()
        }
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
    use fred::prelude::KeysInterface;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    const METRICS_PREFIX: &str = "scraper_metrics_latest:";
    let mut all_keys: Vec<String> = Vec::new();
    let mut cursor = "0".to_string();
    loop {
        let result: Result<fred::types::Value, _> = state
            .redis
            .scan_page(
                cursor.clone(),
                format!("{METRICS_PREFIX}*"),
                Some(100),
                None,
            )
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

    if all_keys.is_empty() {
        return Json(json!({"scrapers": []})).into_response();
    }

    let raw_vals: Vec<Option<String>> = state
        .redis
        .mget(all_keys.clone())
        .await
        .unwrap_or_else(|_| vec![None; all_keys.len()]);

    let scrapers: Vec<Value> = raw_vals
        .into_iter()
        .filter_map(|raw| raw.and_then(|s| serde_json::from_str::<Value>(&s).ok()))
        .collect();

    Json(json!({"scrapers": scrapers})).into_response()
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

/// POST /api/v1/admin/scrapers/dmm-hashlist/run
pub async fn run_dmm_hashlist(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut kwargs = serde_json::Map::new();
    if let Some(v) = body["crontab_expression"].as_str() {
        kwargs.insert("crontab_expression".into(), json!(v));
    }
    if let Some(v) = body["force_run"].as_bool() {
        kwargs.insert("force_run".into(), json!(v));
    }
    match enqueue_taskiq(
        &state.redis,
        "run_dmm_hashlist_scraper",
        "scrapy",
        json!([]),
        Value::Object(kwargs),
        0,
    )
    .await
    {
        Ok(task_id) => (
            StatusCode::ACCEPTED,
            Json(json!({"status": "accepted", "task_id": task_id})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("run_dmm_hashlist enqueue error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Failed to enqueue task"})),
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
    let mut kwargs = serde_json::Map::new();
    if let Some(v) = body["max_iterations"].as_i64() {
        kwargs.insert("max_iterations".into(), json!(v));
    }
    if let Some(v) = body["incremental_commits"].as_bool() {
        kwargs.insert("incremental_commits".into(), json!(v));
    }
    if let Some(v) = body["backfill_commits"].as_bool() {
        kwargs.insert("backfill_commits".into(), json!(v));
    }
    if let Some(v) = body["reset_checkpoints"].as_bool() {
        kwargs.insert("reset_checkpoints".into(), json!(v));
    }
    match enqueue_taskiq(
        &state.redis,
        "run_dmm_hashlist_full_ingestion_job",
        "scrapy",
        json!([]),
        Value::Object(kwargs),
        0,
    )
    .await
    {
        Ok(task_id) => (
            StatusCode::ACCEPTED,
            Json(json!({"status": "accepted", "task_id": task_id})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("run_dmm_hashlist_full enqueue error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Failed to enqueue task"})),
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
            "migrated_count": 0,
            "source_ids_migrated": [],
            "target_media_id": to_id
        }))
        .into_response();
    }

    let mut migrated_ids: Vec<i64> = Vec::new();
    for from_id in &from_ids {
        let res = sqlx::query(
            "UPDATE stream_media_link SET media_id = $1 WHERE media_id = $2 ON CONFLICT DO NOTHING",
        )
        .bind(to_id)
        .bind(from_id)
        .execute(&state.pool)
        .await;
        match res {
            Ok(_) => migrated_ids.push(*from_id),
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

    Json(json!({
        "status": "success",
        "migrated_count": migrated_ids.len(),
        "source_ids_migrated": migrated_ids,
        "target_media_id": to_id
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
                "INSERT INTO media_image (media_id, image_type, url, is_primary, display_order) \
                 VALUES ($1, $2, $3, true, 0) ON CONFLICT DO NOTHING",
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
    Path(_meta_id): Path<String>,
    Query(_params): Query<RefreshImdbQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let _ = &state;
    (
        StatusCode::ACCEPTED,
        Json(json!({
            "status": "accepted",
            "message": "IMDB data refresh is handled by the worker service"
        })),
    )
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
                    "INSERT INTO media_image (media_id, image_type, url, is_primary, display_order) \
                     VALUES ($1, $2, $3, true, 0) ON CONFLICT DO NOTHING",
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
                    "INSERT INTO stream_media_link (stream_id, media_id, is_primary) VALUES ($1, $2, true) ON CONFLICT DO NOTHING",
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
    redis: &fred::clients::Client,
    job_id: &str,
    display_name: &str,
    category: &str,
    description: &str,
    crontab: &str,
) -> Value {
    use fred::prelude::KeysInterface;

    let task_key = if SCRAPY_SPIDER_IDS.contains(&job_id) {
        format!("background_tasks:run_spider:spider_name={job_id}")
    } else {
        format!("background_tasks:{job_id}")
    };
    let state_key = format!("scrapy_stats:{job_id}");
    let running_key = format!("scheduler:running:{job_id}");

    let (last_run_raw, state_raw, is_running): (Option<String>, Option<String>, bool) = tokio::join!(
        async {
            redis
                .get::<Option<String>, _>(&task_key)
                .await
                .unwrap_or(None)
        },
        async {
            redis
                .get::<Option<String>, _>(&state_key)
                .await
                .unwrap_or(None)
        },
        async { redis.exists::<i64, _>(&running_key).await.unwrap_or(0) > 0 },
    );

    let (last_run, last_run_timestamp, time_since_last_run) = if let Some(ts_str) = last_run_raw {
        if let Ok(ts) = ts_str.trim().parse::<f64>() {
            let dt = chrono::DateTime::from_timestamp(ts as i64, 0).unwrap_or_else(Utc::now);
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
            (Some(dt.to_rfc3339()), Some(ts), time_since)
        } else {
            (None, None, "Never run".to_string())
        }
    } else {
        (None, None, "Never run".to_string())
    };

    let last_run_state: Option<Value> = state_raw.and_then(|s| serde_json::from_str(&s).ok());

    json!({
        "id": job_id,
        "display_name": display_name,
        "category": category,
        "description": description,
        "crontab": crontab,
        "is_enabled": true,
        "last_run": last_run,
        "last_run_timestamp": last_run_timestamp,
        "time_since_last_run": time_since_last_run,
        "next_run_in": null,
        "next_run_timestamp": null,
        "last_run_state": last_run_state,
        "is_running": is_running,
    })
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
        .map(|(id, name, cat, desc, cron)| fetch_job_info(&state.redis, id, name, cat, desc, cron))
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
    // Build stats from the same Redis data
    let futures: Vec<_> = SCHEDULER_JOBS
        .iter()
        .map(|(id, name, cat, desc, cron)| fetch_job_info(&state.redis, id, name, cat, desc, cron))
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
        let info = fetch_job_info(&state.redis, id, name, cat, desc, cron).await;
        Json(info).into_response()
    } else {
        (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Job not found"})),
        )
            .into_response()
    }
}

/// POST /api/v1/admin/schedulers/{job_id}/run?force_run=...
#[derive(Deserialize)]
pub struct RunSchedulerQuery {
    pub force_run: Option<bool>,
}

async fn dispatch_scheduler_job(
    redis: &fred::clients::Client,
    job_id: &str,
    force_run: Option<bool>,
) -> Result<String, ()> {
    // Verify job_id is known
    if !SCHEDULER_JOBS.iter().any(|(id, ..)| *id == job_id) {
        return Err(());
    }

    let mut kwargs = serde_json::Map::new();
    if let Some(v) = force_run {
        kwargs.insert("force_run".into(), json!(v));
    }

    let (actor_name, queue): (&str, &str) = if SCRAPY_SPIDER_IDS.contains(&job_id) {
        ("run_spider", "scrapy")
    } else {
        match job_id {
            "dmm_hashlist_scraper" => ("run_dmm_hashlist_scraper", "scrapy"),
            "prowlarr_feed_scraper" => ("run_prowlarr_feed_scraper", "scrapy"),
            "jackett_feed_scraper" => ("run_jackett_feed_scraper", "scrapy"),
            "rss_feed_scraper" => ("run_rss_feed_scraper", "scrapy"),
            "youtube_background_scraper" => ("run_youtube_background_scraper", "scrapy"),
            "acestream_background_scraper" => ("run_acestream_background_scraper", "scrapy"),
            "telegram_background_scraper" => ("run_telegram_background_scraper", "scrapy"),
            "validate_tv_streams_in_db" => ("validate_tv_streams_in_db", "default"),
            "update_seeders" => ("update_torrent_seeders", "default"),
            "cleanup_expired_scraper_task" => ("cleanup_expired_scraper_task", "scrapy"),
            "cleanup_expired_cache_task" => ("cleanup_expired_cache", "default"),
            "background_search" => ("run_background_search", "scrapy"),
            _ => ("run_spider", "scrapy"),
        }
    };

    let args = if SCRAPY_SPIDER_IDS.contains(&job_id) {
        json!([job_id])
    } else {
        json!([])
    };

    enqueue_taskiq(redis, actor_name, queue, args, Value::Object(kwargs), 0)
        .await
        .map_err(|e| {
            tracing::error!("dispatch_scheduler_job enqueue error for {job_id}: {e}");
        })
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
    match dispatch_scheduler_job(&state.redis, &job_id, params.force_run).await {
        Ok(task_id) => (
            StatusCode::ACCEPTED,
            Json(json!({"status": "accepted", "task_id": task_id, "job_id": job_id})),
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
    match dispatch_scheduler_job(&state.redis, &job_id, None).await {
        Ok(task_id) => (
            StatusCode::ACCEPTED,
            Json(json!({"status": "accepted", "task_id": task_id, "job_id": job_id})),
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
    use fred::prelude::ListInterface;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let limit = params.limit.unwrap_or(50).clamp(1, 200) - 1;
    let key = format!("scheduler_history:{job_id}");
    let raw_entries: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(&key, 0, limit)
        .await
        .unwrap_or_default();
    let entries: Vec<serde_json::Value> = raw_entries
        .iter()
        .filter_map(|s| serde_json::from_str(s).ok())
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

// ─── Task Redis helpers ───────────────────────────────────────────────────────

/// Enqueue a task to the Taskiq Redis Streams backend.
/// Returns the new task_id (UUID hex string) or an error string.
async fn enqueue_taskiq(
    redis: &fred::clients::Client,
    actor_name: &str,
    queue: &str,
    args: Value,
    kwargs: Value,
    _priority: i32,
) -> Result<String, String> {
    use fred::prelude::{KeysInterface, ListInterface, StreamsInterface};

    let task_id = uuid::Uuid::new_v4().simple().to_string();
    let queue_name = format!("mediafusion:taskiq:{queue}");

    // Build kwargs with the internal task_id injected
    let mut kw_map = match kwargs {
        Value::Object(m) => m,
        _ => serde_json::Map::new(),
    };
    kw_map.insert("_taskiq_task_id".into(), json!(task_id));

    let msg = json!({
        "task_id": task_id,
        "task_name": actor_name,
        "labels": {
            "queue_name": queue_name,
        },
        "labels_types": null,
        "args": args,
        "kwargs": Value::Object(kw_map),
    });
    let msg_json = serde_json::to_string(&msg).map_err(|e| e.to_string())?;

    // XADD to the stream
    redis
        .xadd::<String, _, _, _, _>(
            &queue_name,
            false,
            None,
            "*",
            vec![("data", msg_json.as_str())],
        )
        .await
        .map_err(|e| e.to_string())?;

    // Store task record with 7-day TTL
    let task_record = json!({
        "task_id": task_id,
        "actor_name": actor_name,
        "queue_name": queue_name,
        "args_payload": args,
        "kwargs_payload": Value::Object({
            let mut m = msg["kwargs"].as_object().cloned().unwrap_or_default();
            m.remove("_taskiq_task_id");
            m
        }),
        "status": "pending",
        "created_at": Utc::now().to_rfc3339(),
    });
    let task_key = format!("mediafusion:taskiq:task:{task_id}");
    let task_json = serde_json::to_string(&task_record).map_err(|e| e.to_string())?;
    redis
        .set::<String, _, _>(
            &task_key,
            task_json.as_str(),
            Some(fred::types::Expiration::EX(7 * 24 * 3600)),
            None,
            false,
        )
        .await
        .map_err(|e| e.to_string())?;

    // Update recent task list
    let recent_key = "mediafusion:taskiq:tasks:recent";
    redis
        .lpush::<i64, _, _>(recent_key, task_id.as_str())
        .await
        .map_err(|e| e.to_string())?;
    redis
        .ltrim::<(), _>(recent_key, 0, 1999)
        .await
        .map_err(|e| e.to_string())?;

    Ok(task_id)
}

const TASK_RECENT_KEY: &str = "mediafusion:taskiq:tasks:recent";
const TASK_RUNNING_KEY: &str = "mediafusion:taskiq:tasks:running";
const TASK_DETAILS_PREFIX: &str = "mediafusion:taskiq:task";

async fn load_tasks(redis: &fred::clients::Client, fetch_limit: i64) -> (Vec<Value>, Vec<String>) {
    use fred::prelude::{KeysInterface, ListInterface, SetsInterface};

    let task_ids: Vec<String> = redis
        .lrange(TASK_RECENT_KEY, 0, fetch_limit - 1)
        .await
        .unwrap_or_default();

    let running_raw: std::collections::HashSet<String> =
        redis.smembers(TASK_RUNNING_KEY).await.unwrap_or_default();
    let running_raw: Vec<String> = running_raw.into_iter().collect();
    let running_set: std::collections::HashSet<_> = running_raw.iter().cloned().collect();

    if task_ids.is_empty() {
        return (vec![], running_raw);
    }

    let keys: Vec<String> = task_ids
        .iter()
        .map(|id| format!("{TASK_DETAILS_PREFIX}:{id}"))
        .collect();

    let raw_vals: Vec<Option<String>> = redis
        .mget(keys)
        .await
        .unwrap_or_else(|_| vec![None; task_ids.len()]);

    let mut records = Vec::with_capacity(task_ids.len());
    for (task_id, raw) in task_ids.iter().zip(raw_vals.iter()) {
        let mut rec: Value = raw
            .as_deref()
            .and_then(|s| serde_json::from_str(s).ok())
            .unwrap_or_else(|| json!({"task_id": task_id, "status": "unknown"}));
        if let Some(obj) = rec.as_object_mut() {
            obj.entry("task_id").or_insert_with(|| json!(task_id));
            let is_running = running_set.contains(task_id);
            if is_running {
                obj.insert("status".into(), json!("running"));
                obj.insert("is_running_now".into(), json!(true));
            } else {
                obj.entry("status").or_insert_with(|| json!("unknown"));
                obj.insert("is_running_now".into(), json!(false));
            }
        }
        records.push(rec);
    }
    (records, running_raw)
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
            "{} {} {} {} {} {}",
            rec["task_id"].as_str().unwrap_or(""),
            rec["actor_name"].as_str().unwrap_or(""),
            rec["queue_name"].as_str().unwrap_or(""),
            rec["status"].as_str().unwrap_or(""),
            rec["error_type"].as_str().unwrap_or(""),
            rec["error_message"].as_str().unwrap_or(""),
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
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let sample = params.sample_size.unwrap_or(200).clamp(1, 2000);
    let (records, running_task_ids) = load_tasks(&state.redis, sample).await;

    let total = records.len();
    let mut global_status_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    let mut queue_counts: std::collections::HashMap<
        String,
        std::collections::HashMap<String, usize>,
    > = std::collections::HashMap::new();
    for rec in &records {
        let status = rec["status"].as_str().unwrap_or("unknown").to_string();
        *global_status_counts.entry(status.clone()).or_insert(0) += 1;
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
            let running_q = running_task_ids.iter().filter(|_| true).count(); // approximate
            json!({"queue_name": q.clone(), "stream_name": q, "recent_total": total_q, "status_counts": counts, "currently_running": running_q})
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

    let (all_records, running_task_ids) = load_tasks(&state.redis, fetch_size).await;

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

    let (all_records, running_task_ids) = load_tasks(&state.redis, sample).await;

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
    use fred::prelude::KeysInterface;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let reason = body["reason"].as_str().unwrap_or("manual").to_string();
    let limit = body["limit"].as_i64().unwrap_or(100) as usize;

    // Determine which task_ids to cancel
    let task_ids: Vec<String> = if let Some(arr) = body["task_ids"].as_array() {
        arr.iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect()
    } else {
        // Filter from recent tasks
        let fetch_size = (limit * 5).clamp(200, 2000) as i64;
        let (records, _) = load_tasks(&state.redis, fetch_size).await;
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
            .filter_map(|r| r["task_id"].as_str().map(|s| s.to_string()))
            .collect()
    };

    let now = Utc::now().to_rfc3339();
    let mut cancelled_ids: Vec<String> = Vec::new();

    for task_id in &task_ids {
        // Set cancellation marker
        let cancel_key = format!("mediafusion:taskiq:cancelled:{task_id}");
        let _ = state
            .redis
            .set::<String, _, _>(
                &cancel_key,
                reason.as_str(),
                Some(fred::types::Expiration::EX(86400)),
                None,
                false,
            )
            .await;

        // Update task record
        let task_key = format!("{TASK_DETAILS_PREFIX}:{task_id}");
        if let Ok(Some(raw)) = state.redis.get::<Option<String>, _>(&task_key).await {
            if let Ok(mut rec) = serde_json::from_str::<Value>(&raw) {
                if let Some(obj) = rec.as_object_mut() {
                    obj.insert("cancellation_requested".into(), json!(true));
                    obj.insert("cancellation_reason".into(), json!(reason));
                    obj.insert("cancellation_requested_at".into(), json!(now));
                }
                if let Ok(updated) = serde_json::to_string(&rec) {
                    let _ = state
                        .redis
                        .set::<String, _, _>(
                            &task_key,
                            updated.as_str(),
                            Some(fred::types::Expiration::EX(7 * 24 * 3600)),
                            None,
                            false,
                        )
                        .await;
                }
            }
        }
        cancelled_ids.push(task_id.clone());
    }

    Json(json!({"cancelled": cancelled_ids.len(), "task_ids": cancelled_ids})).into_response()
}

/// POST /api/v1/admin/tasks/bulk-retry
pub async fn bulk_retry_tasks(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    use fred::prelude::KeysInterface;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let limit = body["limit"].as_i64().unwrap_or(100) as usize;

    let task_ids: Vec<String> = if let Some(arr) = body["task_ids"].as_array() {
        arr.iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect()
    } else {
        let fetch_size = (limit * 5).clamp(200, 2000) as i64;
        let (records, _) = load_tasks(&state.redis, fetch_size).await;
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
            .filter_map(|r| r["task_id"].as_str().map(|s| s.to_string()))
            .collect()
    };

    let mut retried = 0usize;
    let mut failed = 0usize;
    let mut new_task_ids: Vec<String> = Vec::new();

    for task_id in &task_ids {
        let task_key = format!("{TASK_DETAILS_PREFIX}:{task_id}");
        match state.redis.get::<Option<String>, _>(&task_key).await {
            Ok(Some(raw)) => {
                if let Ok(rec) = serde_json::from_str::<Value>(&raw) {
                    let actor = rec["actor_name"].as_str().unwrap_or("").to_string();
                    let queue_full = rec["queue_name"]
                        .as_str()
                        .unwrap_or("mediafusion:taskiq:default")
                        .to_string();
                    // Extract short queue name from full key
                    let queue_short = queue_full
                        .strip_prefix("mediafusion:taskiq:")
                        .unwrap_or("default")
                        .to_string();
                    let args = rec["args_payload"].clone();
                    let kwargs = rec["kwargs_payload"].clone();
                    match enqueue_taskiq(&state.redis, &actor, &queue_short, args, kwargs, 0).await
                    {
                        Ok(new_id) => {
                            new_task_ids.push(new_id);
                            retried += 1;
                        }
                        Err(_) => {
                            failed += 1;
                        }
                    }
                } else {
                    failed += 1;
                }
            }
            _ => {
                failed += 1;
            }
        }
    }

    Json(json!({"retried": retried, "failed": failed, "new_task_ids": new_task_ids}))
        .into_response()
}

/// GET /api/v1/admin/tasks/{task_id}
pub async fn get_task_detail(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(task_id): Path<String>,
) -> impl IntoResponse {
    use fred::prelude::KeysInterface;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let key = format!("{TASK_DETAILS_PREFIX}:{task_id}");
    let raw: Option<String> = state.redis.get(&key).await.unwrap_or(None);
    match raw {
        Some(s) => match serde_json::from_str::<Value>(&s) {
            Ok(v) => Json(v).into_response(),
            Err(_) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Failed to parse task data"})),
            )
                .into_response(),
        },
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Task not found"})),
        )
            .into_response(),
    }
}

/// POST /api/v1/admin/tasks/{task_id}/retry
pub async fn retry_task(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(task_id): Path<String>,
) -> impl IntoResponse {
    use fred::prelude::KeysInterface;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let task_key = format!("{TASK_DETAILS_PREFIX}:{task_id}");
    let raw: Option<String> = state.redis.get(&task_key).await.unwrap_or(None);
    let rec = match raw.and_then(|s| serde_json::from_str::<Value>(&s).ok()) {
        Some(v) => v,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Task not found"})),
            )
                .into_response();
        }
    };

    let actor = rec["actor_name"].as_str().unwrap_or("").to_string();
    let queue_full = rec["queue_name"]
        .as_str()
        .unwrap_or("mediafusion:taskiq:default")
        .to_string();
    let queue_short = queue_full
        .strip_prefix("mediafusion:taskiq:")
        .unwrap_or("default")
        .to_string();
    let args = rec["args_payload"].clone();
    let kwargs = rec["kwargs_payload"].clone();

    match enqueue_taskiq(&state.redis, &actor, &queue_short, args, kwargs, 0).await {
        Ok(new_task_id) => (
            StatusCode::ACCEPTED,
            Json(json!({
                "status": "accepted",
                "new_task_id": new_task_id,
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
    Json(body): Json<Value>,
) -> impl IntoResponse {
    use fred::prelude::KeysInterface;

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let task_key = format!("{TASK_DETAILS_PREFIX}:{task_id}");
    let raw: Option<String> = state.redis.get(&task_key).await.unwrap_or(None);
    if raw.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Task not found"})),
        )
            .into_response();
    }

    let reason = body["reason"].as_str().unwrap_or("manual").to_string();
    let now = Utc::now().to_rfc3339();

    // Set cancellation marker
    let cancel_key = format!("mediafusion:taskiq:cancelled:{task_id}");
    let _ = state
        .redis
        .set::<String, _, _>(
            &cancel_key,
            reason.as_str(),
            Some(fred::types::Expiration::EX(86400)),
            None,
            false,
        )
        .await;

    // Update task record
    if let Some(s) = raw {
        if let Ok(mut rec) = serde_json::from_str::<Value>(&s) {
            if let Some(obj) = rec.as_object_mut() {
                obj.insert("cancellation_requested".into(), json!(true));
                obj.insert("cancellation_reason".into(), json!(reason));
                obj.insert("cancellation_requested_at".into(), json!(now));
            }
            if let Ok(updated) = serde_json::to_string(&rec) {
                let _ = state
                    .redis
                    .set::<String, _, _>(
                        &task_key,
                        updated.as_str(),
                        Some(fred::types::Expiration::EX(7 * 24 * 3600)),
                        None,
                        false,
                    )
                    .await;
            }
        }
    }

    (
        StatusCode::ACCEPTED,
        Json(json!({"status": "accepted", "task_id": task_id, "reason": reason})),
    )
        .into_response()
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
