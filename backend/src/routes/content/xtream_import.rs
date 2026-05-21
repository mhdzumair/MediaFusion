/// Xtream Codes API import endpoints.
///
/// Routes (prefix /api/v1/import):
///   POST /xtream/analyze  → analyze_xtream
///   POST /xtream          → import_xtream
use std::sync::Arc;

use axum::{
    extract::{Request, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use fred::prelude::*;
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::Sha256;
use uuid::Uuid;

use crate::state::AppState;

// ─── Auth ─────────────────────────────────────────────────────────────────────

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

// ─── Request / Response types ─────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct AnalyzeXtreamRequest {
    pub server_url: String,
    pub username: String,
    pub password: String,
}

#[derive(Deserialize)]
pub struct ImportXtreamRequest {
    pub redis_key: String,
    pub source_name: Option<String>,
    pub import_live: Option<bool>,
    pub import_vod: Option<bool>,
    pub import_series: Option<bool>,
    pub is_public: Option<bool>,
    #[serde(default)]
    pub save_source: bool,
    pub live_category_ids: Option<Vec<String>>,
    pub vod_category_ids: Option<Vec<String>>,
    pub series_category_ids: Option<Vec<String>>,
}

fn filter_xtream_by_categories(
    items: &[serde_json::Value],
    allowed: Option<&[String]>,
) -> Vec<serde_json::Value> {
    let Some(ids) = allowed else {
        return items.to_vec();
    };
    if ids.is_empty() {
        return items.to_vec();
    }
    items
        .iter()
        .filter(|s| {
            let cat_id = s.get("category_id").and_then(|v| {
                v.as_str()
                    .map(str::to_string)
                    .or_else(|| v.as_i64().map(|n| n.to_string()))
            });
            cat_id
                .as_ref()
                .map(|id| ids.iter().any(|a| a == id))
                .unwrap_or(false)
        })
        .cloned()
        .collect()
}

/// Cached Xtream data stored in Redis.
#[derive(Serialize, Deserialize)]
struct XtreamCachedData {
    server_url: String,
    username: String,
    password: String,
    live_categories: Vec<serde_json::Value>,
    vod_categories: Vec<serde_json::Value>,
    series_categories: Vec<serde_json::Value>,
    live_streams: Vec<serde_json::Value>,
    vod_streams: Vec<serde_json::Value>,
    series: Vec<serde_json::Value>,
}

// ─── Xtream API helpers ───────────────────────────────────────────────────────

async fn fetch_xtream_json(
    http: &reqwest::Client,
    server_url: &str,
    username: &str,
    password: &str,
    action: &str,
) -> Vec<serde_json::Value> {
    let url = format!(
        "{}/player_api.php?username={}&password={}&action={}",
        server_url.trim_end_matches('/'),
        urlencoding::encode(username),
        urlencoding::encode(password),
        action
    );
    match http
        .get(&url)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.json().await.unwrap_or_default(),
        _ => Vec::new(),
    }
}

use crate::routes::content::iptv_import::{self, IptvImportCtx};

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/import/xtream/analyze
pub async fn analyze_xtream(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    // Parse JSON body
    let body_bytes = match axum::body::to_bytes(req.into_body(), 1024 * 1024).await {
        Ok(b) => b,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Failed to read body"})),
            )
                .into_response();
        }
    };

    let req_body: AnalyzeXtreamRequest = match serde_json::from_slice(&body_bytes) {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": format!("Invalid JSON: {e}")})),
            )
                .into_response();
        }
    };

    let server = req_body.server_url.trim_end_matches('/').to_string();
    let user = &req_body.username;
    let pass = &req_body.password;

    // Fetch categories in parallel
    let (live_cats, vod_cats, series_cats) = tokio::join!(
        fetch_xtream_json(&state.http, &server, user, pass, "get_live_categories"),
        fetch_xtream_json(&state.http, &server, user, pass, "get_vod_categories"),
        fetch_xtream_json(&state.http, &server, user, pass, "get_series_categories"),
    );

    // Fetch stream lists
    let (live_streams, vod_streams, series_list) = tokio::join!(
        fetch_xtream_json(&state.http, &server, user, pass, "get_live_streams"),
        fetch_xtream_json(&state.http, &server, user, pass, "get_vod_streams"),
        fetch_xtream_json(&state.http, &server, user, pass, "get_series"),
    );

    // Build category summaries with counts — count by category_id first (O(N))
    fn count_by_category(
        streams: &[serde_json::Value],
    ) -> std::collections::HashMap<String, usize> {
        let mut counts = std::collections::HashMap::new();
        for s in streams {
            let cat = s
                .get("category_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            *counts.entry(cat).or_insert(0) += 1;
        }
        counts
    }
    let live_counts = count_by_category(&live_streams);
    let vod_counts = count_by_category(&vod_streams);
    let series_counts = count_by_category(&series_list);

    let live_summary: Vec<serde_json::Value> = live_cats
        .iter()
        .map(|c| {
            let cat_id = c.get("category_id").and_then(|v| v.as_str()).unwrap_or("");
            json!({
                "category_id": cat_id,
                "category_name": c.get("category_name").and_then(|v| v.as_str()).unwrap_or(""),
                "count": live_counts.get(cat_id).copied().unwrap_or(0),
            })
        })
        .collect();

    let vod_summary: Vec<serde_json::Value> = vod_cats
        .iter()
        .map(|c| {
            let cat_id = c.get("category_id").and_then(|v| v.as_str()).unwrap_or("");
            json!({
                "category_id": cat_id,
                "category_name": c.get("category_name").and_then(|v| v.as_str()).unwrap_or(""),
                "count": vod_counts.get(cat_id).copied().unwrap_or(0),
            })
        })
        .collect();

    let series_summary: Vec<serde_json::Value> = series_cats
        .iter()
        .map(|c| {
            let cat_id = c.get("category_id").and_then(|v| v.as_str()).unwrap_or("");
            json!({
                "category_id": cat_id,
                "category_name": c.get("category_name").and_then(|v| v.as_str()).unwrap_or(""),
                "count": series_counts.get(cat_id).copied().unwrap_or(0),
            })
        })
        .collect();

    // Cache full data in Redis
    let redis_key = format!("xtream_analyze_{}", Uuid::new_v4());
    let cached = XtreamCachedData {
        server_url: server.clone(),
        username: req_body.username.clone(),
        password: req_body.password.clone(),
        live_categories: live_cats,
        vod_categories: vod_cats,
        series_categories: series_cats,
        live_streams,
        vod_streams,
        series: series_list,
    };

    if let Ok(json_str) = serde_json::to_string(&cached) {
        let _ = state
            .redis
            .set::<(), _, _>(
                &redis_key,
                json_str,
                Some(Expiration::EX(3600)),
                None,
                false,
            )
            .await;
    }

    Json(json!({
        "redis_key": redis_key,
        "server_url": server,
        "live_categories": live_summary,
        "vod_categories": vod_summary,
        "series_categories": series_summary,
        "total_live": cached.live_streams.len(),
        "total_vod": cached.vod_streams.len(),
        "total_series": cached.series.len(),
    }))
    .into_response()
}

/// POST /api/v1/import/xtream
pub async fn import_xtream(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
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

    if !state.config.enable_iptv_import {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "IPTV import feature is disabled on this server."})),
        )
            .into_response();
    }

    // Parse body
    let body_bytes = match axum::body::to_bytes(req.into_body(), 1024 * 1024).await {
        Ok(b) => b,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Failed to read body"})),
            )
                .into_response();
        }
    };

    let req_body: ImportXtreamRequest = match serde_json::from_slice(&body_bytes) {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": format!("Invalid JSON: {e}")})),
            )
                .into_response();
        }
    };

    // Load cached data from Redis
    let cached_str: Option<String> = state.redis.get(&req_body.redis_key).await.unwrap_or(None);
    let cached: XtreamCachedData = match cached_str {
        Some(s) => match serde_json::from_str(&s) {
            Ok(d) => d,
            Err(_) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": "Cached analysis data is invalid"})),
                )
                    .into_response();
            }
        },
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Analysis cache expired. Please re-run analyze_xtream first."})),
            )
                .into_response();
        }
    };

    let source_name = req_body
        .source_name
        .as_deref()
        .unwrap_or("Xtream Import")
        .to_string();
    let import_live = req_body.import_live.unwrap_or(true);
    let import_vod = req_body.import_vod.unwrap_or(true);
    let import_series = req_body.import_series.unwrap_or(true);

    let mut is_public = req_body.is_public.unwrap_or(false);
    if !state.config.allow_public_iptv_sharing {
        is_public = false;
    }

    let live_streams =
        filter_xtream_by_categories(&cached.live_streams, req_body.live_category_ids.as_deref());
    let vod_streams =
        filter_xtream_by_categories(&cached.vod_streams, req_body.vod_category_ids.as_deref());
    let series_list =
        filter_xtream_by_categories(&cached.series, req_body.series_category_ids.as_deref());

    let total_estimate = (if import_live { live_streams.len() } else { 0 })
        + (if import_vod { vod_streams.len() } else { 0 })
        + (if import_series { series_list.len() } else { 0 });

    if total_estimate == 0 {
        return Json(json!({
            "status": "success",
            "stats": iptv_import::IptvImportStats::default(),
            "message": "Nothing to import (all import flags disabled)",
        }))
        .into_response();
    }

    let ctx = IptvImportCtx::from_state(&state);

    if total_estimate > 100 {
        let job_id = Uuid::new_v4().to_string();
        let job_key = format!("import_job:{job_id}");
        iptv_import::update_import_job_full(
            &state.redis,
            &job_key,
            "queued",
            0,
            total_estimate,
            &iptv_import::IptvImportStats::default(),
            Some(user_id),
            Some("xtream"),
            None,
        )
        .await;

        let pool = state.pool.clone();
        let http = state.http.clone();
        let redis = state.redis.clone();
        let tmdb = state.config.tmdb_api_key.clone();
        let tvdb = state.config.tvdb_api_key.clone();
        let cinemeta = state.config.imdb_cinemeta_fallback_enabled;
        let secret_key = state.config.secret_key;
        let server = cached.server_url.clone();
        let user_c = cached.username.clone();
        let pass = cached.password.clone();
        let live = live_streams.clone();
        let vod = vod_streams.clone();
        let series = series_list.clone();
        let label = source_name.clone();
        let save_source = req_body.save_source;
        let live_cat_ids = req_body.live_category_ids.clone();
        let vod_cat_ids = req_body.vod_category_ids.clone();
        let series_cat_ids = req_body.series_category_ids.clone();

        tokio::spawn(async move {
            iptv_import::update_import_job_full(
                &redis,
                &job_key,
                "processing",
                0,
                total_estimate,
                &iptv_import::IptvImportStats::default(),
                Some(user_id),
                Some("xtream"),
                None,
            )
            .await;

            let ctx_bg = iptv_import::IptvImportCtx {
                pool: &pool,
                http: &http,
                tmdb_api_key: tmdb.as_deref(),
                tvdb_api_key: tvdb.as_deref(),
                cinemeta_enabled: cinemeta,
            };
            let stats = iptv_import::run_xtream_import_batch(
                &ctx_bg,
                &server,
                &user_c,
                &pass,
                &label,
                user_id,
                is_public,
                import_live,
                import_vod,
                import_series,
                &live,
                &vod,
                &series,
            )
            .await;

            let mut source_id: Option<i32> = None;
            if save_source {
                let creds = serde_json::json!({
                    "username": user_c,
                    "password": pass,
                });
                if let Some(enc) = crate::crypto::profile::encrypt_secrets(&creds, &secret_key) {
                    source_id = iptv_import::save_xtream_iptv_source(
                        &pool,
                        user_id,
                        &label,
                        &server,
                        &enc,
                        is_public,
                        import_live,
                        import_vod,
                        import_series,
                        live_cat_ids.as_deref(),
                        vod_cat_ids.as_deref(),
                        series_cat_ids.as_deref(),
                        &stats,
                    )
                    .await
                    .ok();
                }
            }

            let mut job_body = serde_json::json!({
                "status": "completed",
                "progress": total_estimate,
                "total": total_estimate,
                "stats": stats,
                "user_id": user_id,
                "source_type": "xtream",
            });
            if let Some(sid) = source_id {
                job_body["source_id"] = serde_json::json!(sid);
                job_body["source_saved"] = serde_json::json!(true);
            }
            let _ = redis
                .set::<(), _, _>(
                    &job_key,
                    job_body.to_string(),
                    Some(fred::types::Expiration::EX(86400)),
                    None,
                    false,
                )
                .await;
        });

        let _: Result<(), _> = state.redis.del(&req_body.redis_key).await;

        return (
            StatusCode::ACCEPTED,
            Json(json!({
                "status": "processing",
                "job_id": job_id,
                "total": total_estimate,
                "message": format!("Import started for {total_estimate} items"),
            })),
        )
            .into_response();
    }

    let stats = iptv_import::run_xtream_import_batch(
        &ctx,
        &cached.server_url,
        &cached.username,
        &cached.password,
        &source_name,
        user_id,
        is_public,
        import_live,
        import_vod,
        import_series,
        &live_streams,
        &vod_streams,
        &series_list,
    )
    .await;

    let mut source_id: Option<i32> = None;
    if req_body.save_source {
        let creds = serde_json::json!({
            "username": cached.username,
            "password": cached.password,
        });
        if let Some(enc) = crate::crypto::profile::encrypt_secrets(&creds, &state.config.secret_key)
        {
            source_id = iptv_import::save_xtream_iptv_source(
                &state.pool,
                user_id,
                &source_name,
                &cached.server_url,
                &enc,
                is_public,
                import_live,
                import_vod,
                import_series,
                req_body.live_category_ids.as_deref(),
                req_body.vod_category_ids.as_deref(),
                req_body.series_category_ids.as_deref(),
                &stats,
            )
            .await
            .ok();
        }
    }

    let _: Result<(), _> = state.redis.del(&req_body.redis_key).await;

    Json(json!({
        "status": "success",
        "stats": stats,
        "total": total_estimate,
        "source_saved": source_id.is_some(),
        "source_id": source_id,
    }))
    .into_response()
}
