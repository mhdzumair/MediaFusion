/// Kodi device pairing and setup routes.
///
/// Routes (prefix /api/v1/kodi):
///   POST /generate-setup-code        → generate_setup_code
///   GET  /qr-code/{code}             → get_qr_code
///   GET  /qr-code/{secret_str}/{code}→ get_qr_code_with_secret
///   POST /associate-manifest         → associate_manifest
///   GET  /get-manifest/{code}        → get_manifest
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use fred::prelude::*;
use serde::Deserialize;
use serde_json::json;

use crate::routes::auth::validate_access_token;
use crate::state::AppState;

// ─── Request types ────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct AssociateManifestRequest {
    pub code: String,
    pub manifest_url: String,
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

fn validate_api_key(headers: &HeaderMap, api_password: &Option<String>, secret_key: &str) -> bool {
    match api_password {
        None => true, // public instance — no key required
        Some(pw) => {
            // Accept X-API-Key header
            let api_key_ok = headers
                .get("x-api-key")
                .and_then(|v| v.to_str().ok())
                .map(|k| k == pw.as_str())
                .unwrap_or(false);
            // Also accept a valid Bearer token (logged-in user on private instance)
            api_key_ok || validate_access_token(headers, secret_key).is_some()
        }
    }
}

// ─── Handlers ────────────────────────────────────────────────────────────────

/// POST /api/v1/kodi/generate-setup-code
pub async fn generate_setup_code(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    // On private instances validate API key
    if state.config.api_password.is_some()
        && !validate_api_key(
            &headers,
            &state.config.api_password,
            &state.config.secret_key_raw,
        )
    {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Authentication required"})),
        )
            .into_response();
    }

    // Generate 6-char hex code
    let code = {
        let mut bytes = [0u8; 3];
        use rand_core::{OsRng, RngCore};
        OsRng.fill_bytes(&mut bytes);
        bytes.iter().map(|b| format!("{b:02x}")).collect::<String>()
    };

    let configure_url = format!("{}/app/configure?kodi_code={}", state.config.host_url, code);
    let qr_code_url = format!("{}/api/v1/kodi/qr-code/{}", state.config.host_url, code);

    // Store code in Redis with 5 min TTL
    let key = format!("setup_code:{code}");
    if let Err(e) = state
        .redis
        .set::<(), _, _>(&key, "1", Some(Expiration::EX(300)), None, false)
        .await
    {
        tracing::error!("kodi setup code redis set: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    Json(json!({
        "code": code,
        "configure_url": configure_url,
        "qr_code_url": qr_code_url,
        "expires_in": 300,
    }))
    .into_response()
}

/// GET /api/v1/kodi/qr-code/{code}
pub async fn get_qr_code(
    State(state): State<Arc<AppState>>,
    Path(code): Path<String>,
) -> impl IntoResponse {
    let key = format!("setup_code:{code}");
    let exists: bool = state
        .redis
        .exists::<i64, _>(&key)
        .await
        .map(|n| n > 0)
        .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Invalid setup code"})),
        )
            .into_response();
    }

    // Return the configure URL as JSON (QR image generation requires native image crate, skip for now)
    let configure_url = format!("{}/app/configure?kodi_code={}", state.config.host_url, code);
    Json(json!({
        "configure_url": configure_url,
        "message": "QR code generation requires Python service",
    }))
    .into_response()
}

/// GET /api/v1/kodi/qr-code/{secret_str}/{code}
pub async fn get_qr_code_with_secret(
    State(state): State<Arc<AppState>>,
    Path((secret_str, code)): Path<(String, String)>,
) -> impl IntoResponse {
    let key = format!("setup_code:{code}");
    let exists: bool = state
        .redis
        .exists::<i64, _>(&key)
        .await
        .map(|n| n > 0)
        .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Invalid setup code"})),
        )
            .into_response();
    }

    let configure_url = format!(
        "{}/app/configure?kodi_code={}&secret_str={}",
        state.config.host_url, code, secret_str
    );
    Json(json!({
        "configure_url": configure_url,
        "message": "QR code generation requires Python service",
    }))
    .into_response()
}

/// POST /api/v1/kodi/associate-manifest
pub async fn associate_manifest(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<AssociateManifestRequest>,
) -> impl IntoResponse {
    if state.config.api_password.is_some()
        && !validate_api_key(
            &headers,
            &state.config.api_password,
            &state.config.secret_key_raw,
        )
    {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Invalid or missing API key"})),
        )
            .into_response();
    }

    // Verify the setup code exists
    let setup_key = format!("setup_code:{}", body.code);
    let exists: bool = state
        .redis
        .exists::<i64, _>(&setup_key)
        .await
        .map(|n| n > 0)
        .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Invalid setup code"})),
        )
            .into_response();
    }

    // Store manifest URL with 5 min TTL
    let manifest_key = format!("manifest:{}", body.code);
    if let Err(e) = state
        .redis
        .set::<(), _, _>(
            &manifest_key,
            body.manifest_url.as_str(),
            Some(Expiration::EX(300)),
            None,
            false,
        )
        .await
    {
        tracing::error!("associate_manifest redis set: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    Json(json!({"status": "success"})).into_response()
}

/// GET /api/v1/kodi/get-manifest/{code}
pub async fn get_manifest(
    State(state): State<Arc<AppState>>,
    Path(code): Path<String>,
) -> impl IntoResponse {
    let manifest_key = format!("manifest:{code}");
    let manifest_url: Option<String> = state
        .redis
        .get::<Option<String>, _>(&manifest_key)
        .await
        .unwrap_or(None);

    match manifest_url {
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Manifest URL not found"})),
        )
            .into_response(),
        Some(url) => {
            // Delete both keys after retrieval (one-time use)
            let setup_key = format!("setup_code:{code}");
            let _ = state
                .redis
                .del::<i64, _>(vec![setup_key, manifest_key.clone()])
                .await;

            // Extract secret_string from manifest URL (second-to-last path segment)
            let parts: Vec<&str> = url.trim_end_matches('/').split('/').collect();
            let secret_string = if parts.len() >= 2 {
                parts[parts.len() - 2].to_string()
            } else {
                url.clone()
            };

            Json(json!({"secret_string": secret_string})).into_response()
        }
    }
}
