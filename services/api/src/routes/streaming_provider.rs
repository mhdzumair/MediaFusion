/// Streaming provider debrid cache status and submission.
///
/// Routes:
///   POST /streaming_provider/cache/status
///   POST /streaming_provider/cache/submit
///
/// These mirror Python's cache_helpers: Redis hash `debrid_cache:{service}`
/// stores info_hash → unix expiry timestamp.
use std::sync::Arc;

use axum::{extract::State, http::StatusCode, response::IntoResponse, Json};
use chrono::Utc;
use fred::prelude::HashesInterface;
use serde::{Deserialize, Serialize};

use crate::state::AppState;

const CACHE_KEY_PREFIX: &str = "debrid_cache:";
const EXPIRY_DAYS_SECS: i64 = 7 * 86400;

#[derive(Deserialize)]
pub struct CacheStatusRequest {
    pub service: String,
    pub info_hashes: Vec<String>,
}

#[derive(Serialize)]
pub struct CacheStatusResponse {
    pub cached_status: std::collections::HashMap<String, bool>,
}

#[derive(Deserialize)]
pub struct CacheSubmitRequest {
    pub service: String,
    pub info_hashes: Vec<String>,
}

#[derive(Serialize)]
pub struct CacheSubmitResponse {
    pub success: bool,
    pub message: String,
}

pub async fn check_cache_status(
    State(state): State<Arc<AppState>>,
    Json(req): Json<CacheStatusRequest>,
) -> impl IntoResponse {
    if req.info_hashes.is_empty() {
        return Json(CacheStatusResponse {
            cached_status: std::collections::HashMap::new(),
        });
    }

    let service = normalize_service(&req.service);
    let cache_key = format!("{CACHE_KEY_PREFIX}{service}");
    let now = Utc::now().timestamp();

    let fields: Vec<String> = req.info_hashes.clone();
    let timestamps: Vec<Option<String>> = state
        .redis
        .hmget(&cache_key, fields)
        .await
        .unwrap_or_else(|_| vec![None; req.info_hashes.len()]);

    let mut cached_status = std::collections::HashMap::new();
    let mut expired: Vec<String> = Vec::new();

    for (hash, ts_opt) in req.info_hashes.iter().zip(timestamps.iter()) {
        match ts_opt {
            Some(ts_str) => {
                let expiry: i64 = ts_str.parse().unwrap_or(0);
                if expiry > now {
                    cached_status.insert(hash.clone(), true);
                } else {
                    expired.push(hash.clone());
                    cached_status.insert(hash.clone(), false);
                }
            }
            None => {
                cached_status.insert(hash.clone(), false);
            }
        }
    }

    // Clean up expired entries (best-effort)
    if !expired.is_empty() {
        let _ = state.redis.hdel::<(), _, _>(&cache_key, expired).await;
    }

    Json(CacheStatusResponse { cached_status })
}

pub async fn submit_cached_hashes(
    State(state): State<Arc<AppState>>,
    Json(req): Json<CacheSubmitRequest>,
) -> impl IntoResponse {
    if req.info_hashes.is_empty() {
        return (
            StatusCode::OK,
            Json(CacheSubmitResponse {
                success: true,
                message: "No info hashes provided".into(),
            }),
        );
    }

    let service = normalize_service(&req.service);
    let cache_key = format!("{CACHE_KEY_PREFIX}{service}");
    let expiry_ts = (Utc::now().timestamp() + EXPIRY_DAYS_SECS).to_string();

    // Build mapping: info_hash → expiry timestamp
    let mapping: Vec<(String, String)> = req
        .info_hashes
        .iter()
        .map(|h| (h.clone(), expiry_ts.clone()))
        .collect();

    let result = state.redis.hset::<(), _, _>(&cache_key, mapping).await;

    match result {
        Ok(_) => (
            StatusCode::OK,
            Json(CacheSubmitResponse {
                success: true,
                message: format!(
                    "Stored {} cached info hashes for {}",
                    req.info_hashes.len(),
                    service
                ),
            }),
        ),
        Err(e) => {
            tracing::error!("submit_cached_hashes Redis error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(CacheSubmitResponse {
                    success: false,
                    message: "Error storing cached info hashes".into(),
                }),
            )
        }
    }
}

/// StremThru uses the store name as service name when set.
fn normalize_service(service: &str) -> &str {
    service
}
