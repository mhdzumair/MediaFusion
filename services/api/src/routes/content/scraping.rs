/// On-demand scrape trigger endpoints.
///
/// These proxy to Python if `python_proxy_url` is set, otherwise return 503.
///
/// Routes (prefix /api/v1/scraping):
///   GET  /scrapers               → list_scrapers
///   GET  /{media_id}/status      → get_scrape_status
///   POST /{media_id}/scrape      → trigger_scrape

use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, Request, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

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

// ─── Proxy helper ─────────────────────────────────────────────────────────────

async fn proxy_to_python(
    state: &Arc<AppState>,
    method: reqwest::Method,
    path: &str,
    query: Option<&str>,
    headers: &HeaderMap,
    body: Vec<u8>,
) -> Response {
    let base = match &state.config.python_proxy_url {
        Some(u) => u.clone(),
        None => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"detail": "Scraping service not available in this deployment"})),
            )
                .into_response();
        }
    };

    let url = if let Some(q) = query.filter(|q| !q.is_empty()) {
        format!("{base}{path}?{q}")
    } else {
        format!("{base}{path}")
    };

    let mut req = state.http.request(method, &url);

    // Forward relevant headers
    for (key, val) in headers.iter() {
        let name = key.as_str().to_lowercase();
        if matches!(name.as_str(), "authorization" | "content-type" | "accept") {
            if let Ok(v) = val.to_str() {
                req = req.header(key.as_str(), v);
            }
        }
    }

    if !body.is_empty() {
        req = req.body(body);
    }

    match req.send().await {
        Ok(resp) => {
            let status = StatusCode::from_u16(resp.status().as_u16())
                .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
            let bytes = resp.bytes().await.unwrap_or_default();
            (status, Body::from(bytes)).into_response()
        }
        Err(e) => {
            tracing::error!("proxy_to_python error: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "Failed to reach scraping service"})),
            )
                .into_response()
        }
    }
}

// ─── Query ────────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ScrapeStatusQuery {
    pub media_type: Option<String>,
    pub season: Option<i32>,
    pub episode: Option<i32>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/scraping/scrapers
pub async fn list_scrapers(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response();
    }
    let query = req.uri().query().unwrap_or("").to_string();
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/scraping/scrapers",
        Some(&query),
        &headers,
        vec![],
    )
    .await
}

/// GET /api/v1/scraping/{media_id}/status
pub async fn get_scrape_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response();
    }
    let query = req.uri().query().unwrap_or("").to_string();
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        &format!("/api/v1/scraping/{media_id}/status"),
        Some(&query),
        &headers,
        vec![],
    )
    .await
}

/// POST /api/v1/scraping/{media_id}/scrape
pub async fn trigger_scrape(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response();
    }
    let query = req.uri().query().unwrap_or("").to_string();
    let body = axum::body::to_bytes(req.into_body(), 1024 * 1024)
        .await
        .unwrap_or_default()
        .to_vec();
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        &format!("/api/v1/scraping/{media_id}/scrape"),
        Some(&query),
        &headers,
        body,
    )
    .await
}
