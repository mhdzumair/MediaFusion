use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Json},
};
use serde_json::{json, Value};

use crate::{crypto, models::user_data::UserData, providers::validator, state::AppState};

/// POST /encrypt-user-data
///
/// Accepts a UserData JSON object and returns a D- prefixed encrypted secret string.
/// Mirrors Python's `POST /encrypt-user-data` in api/routers/stremio/config.py.
pub async fn handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    let user_data: UserData = match serde_json::from_value(body.clone()) {
        Ok(u) => u,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"status": "error", "message": format!("invalid user data: {e}")})),
            )
                .into_response();
        }
    };

    let default_nzbdav = state
        .config
        .default_nzbdav_url
        .as_ref()
        .zip(state.config.default_nzbdav_api_key.as_ref())
        .map(|(url, key)| {
            json!({
                "url": url,
                "api_key": key,
            })
        });

    let user_ip = validator::client_ip_from_headers(&headers);
    let (no_proxy, excluded) = state.proxy_bypass_clients();
    let validation = validator::validate_provider_credentials(
        &state.http,
        no_proxy,
        excluded,
        &user_data,
        user_ip.as_deref(),
        default_nzbdav.as_ref(),
    )
    .await;
    if let Some(msg) = validator::validation_error_response(&validation) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"status": "error", "message": msg})),
        )
            .into_response();
    }

    let json_str = match serde_json::to_string(&body) {
        Ok(s) => s,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": format!("invalid JSON: {e}")})),
            )
                .into_response();
        }
    };

    match crypto::encrypt_user_data(&json_str, &state.config.secret_key) {
        Ok(encrypted) => Json(json!({
            "status": "success",
            "encrypted_str": encrypted,
        }))
        .into_response(),
        Err(e) => {
            tracing::warn!("encrypt_user_data failed: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "encryption failed"})),
            )
                .into_response()
        }
    }
}

/// GET /decrypt-user-data/{secret_str}
pub async fn decrypt_handler(
    State(state): State<Arc<AppState>>,
    Path(secret_str): Path<String>,
) -> impl IntoResponse {
    let raw = crate::crypto::decrypt_user_data(&secret_str, &state.config.secret_key);
    match raw {
        Ok(val) => Json(json!({"status": "success", "data": val})).into_response(),
        Err(e) => {
            tracing::debug!("decrypt_user_data failed: {e}");
            (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "decryption failed"})),
            )
                .into_response()
        }
    }
}
