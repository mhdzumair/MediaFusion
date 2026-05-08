/// Poster image upload endpoints.
///
/// If `python_proxy_url` is set, multipart requests are forwarded to Python.
/// Otherwise returns 503.
///
/// Routes (prefix /api/v1/import):
///   POST /images/upload   → upload_image
///   GET  /images/{key}    → get_uploaded_image

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

async fn proxy(
    state: &Arc<AppState>,
    method: reqwest::Method,
    path: &str,
    query: &str,
    headers: &HeaderMap,
    body: Vec<u8>,
) -> Response {
    let base = match &state.config.python_proxy_url {
        Some(u) => u.clone(),
        None => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"detail": "Image upload service not available in this deployment"})),
            )
                .into_response();
        }
    };

    let url = if query.is_empty() {
        format!("{base}{path}")
    } else {
        format!("{base}{path}?{query}")
    };

    let mut req = state.http.request(method, &url);
    for (key, val) in headers.iter() {
        let name = key.as_str().to_lowercase();
        if matches!(name.as_str(), "authorization" | "accept" | "content-type" | "content-length") {
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
            let ct = resp
                .headers()
                .get("content-type")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("application/octet-stream")
                .to_string();
            let bytes = resp.bytes().await.unwrap_or_default();
            (status, [(axum::http::header::CONTENT_TYPE, ct)], Body::from(bytes)).into_response()
        }
        Err(e) => {
            tracing::error!("image_upload proxy error: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "Failed to reach image upload service"})),
            )
                .into_response()
        }
    }
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/import/images/upload
pub async fn upload_image(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response();
    }
    let q = req.uri().query().unwrap_or("").to_string();
    // Forward content-type (multipart boundary is embedded)
    let body = axum::body::to_bytes(req.into_body(), 20 * 1024 * 1024)
        .await
        .unwrap_or_default()
        .to_vec();
    proxy(&state, reqwest::Method::POST, "/api/v1/import/images/upload", &q, &headers, body).await
}

/// GET /api/v1/import/images/{key}
pub async fn get_uploaded_image(
    State(state): State<Arc<AppState>>,
    Path(key): Path<String>,
    req: Request,
) -> Response {
    let headers = req.headers().clone();
    let q = req.uri().query().unwrap_or("").to_string();
    proxy(
        &state,
        reqwest::Method::GET,
        &format!("/api/v1/import/images/{key}"),
        &q,
        &headers,
        vec![],
    )
    .await
}
