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
use hmac::{Hmac, Mac};
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

use crate::routes::content::m3u_import::import_tv_channel;

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

    if !import_live {
        return Json(json!({
            "status": "success",
            "imported": 0,
            "skipped": 0,
            "message": "Nothing to import (import_live=false)",
        }))
        .into_response();
    }

    let live_streams = cached.live_streams;
    let total = live_streams.len();

    // For >100 items, background processing
    if total > 100 {
        let job_id = Uuid::new_v4().to_string();
        let job_key = format!("import_job:{job_id}");
        let job_status = json!({
            "status": "processing",
            "progress": 0,
            "total": total,
        });
        let _ = state
            .redis
            .set::<(), _, _>(
                &job_key,
                job_status.to_string(),
                Some(Expiration::EX(86400)),
                None,
                false,
            )
            .await;

        let pool = state.pool.clone();
        let redis = state.redis.clone();
        let server_url = cached.server_url.clone();
        let username = cached.username.clone();
        let password = cached.password.clone();

        tokio::spawn(async move {
            let mut imported = 0usize;
            let mut skipped = 0usize;
            for (i, stream) in live_streams.iter().enumerate() {
                let name = stream
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Unknown");
                let stream_id = stream
                    .get("stream_id")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0);
                let logo = stream
                    .get("stream_icon")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty());
                let stream_url = format!(
                    "{}/live/{}/{}/{}.m3u8",
                    server_url, username, password, stream_id
                );

                if import_tv_channel(&pool, name, &stream_url, logo, &source_name).await {
                    imported += 1;
                } else {
                    skipped += 1;
                }

                if (i + 1) % 10 == 0 {
                    let progress = json!({
                        "status": "processing",
                        "progress": i + 1,
                        "total": live_streams.len(),
                    });
                    let _ = redis
                        .set::<(), _, _>(
                            &job_key,
                            progress.to_string(),
                            Some(Expiration::EX(86400)),
                            None,
                            false,
                        )
                        .await;
                }
            }
            let done = json!({
                "status": "completed",
                "progress": live_streams.len(),
                "total": live_streams.len(),
                "stats": { "imported": imported, "skipped": skipped },
            });
            let _ = redis
                .set::<(), _, _>(
                    &job_key,
                    done.to_string(),
                    Some(Expiration::EX(86400)),
                    None,
                    false,
                )
                .await;
        });

        return (
            StatusCode::ACCEPTED,
            Json(json!({
                "status": "processing",
                "job_id": job_id,
                "total": total,
                "message": format!("Import started for {total} live streams"),
            })),
        )
            .into_response();
    }

    // Small batch: synchronous
    let server_url = &cached.server_url;
    let username = &cached.username;
    let password = &cached.password;
    let mut imported = 0usize;
    let mut skipped = 0usize;

    for stream in &live_streams {
        let name = stream
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown");
        let stream_id = stream
            .get("stream_id")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let logo = stream
            .get("stream_icon")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty());
        let stream_url = format!(
            "{}/live/{}/{}/{}.m3u8",
            server_url, username, password, stream_id
        );

        if import_tv_channel(&state.pool, name, &stream_url, logo, &source_name).await {
            imported += 1;
        } else {
            skipped += 1;
        }
    }

    // Clean up Redis key
    let _: Result<(), _> = state.redis.del(&req_body.redis_key).await;

    Json(json!({
        "status": "success",
        "imported": imported,
        "skipped": skipped,
        "total": total,
    }))
    .into_response()
}
