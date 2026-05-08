use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde_json::{json, Value};

use crate::{crypto, state::AppState};

/// POST /encrypt-user-data
///
/// Accepts a UserData JSON object and returns a D- prefixed encrypted secret string.
/// Mirrors Python's `POST /encrypt-user-data` in api/routers/stremio/config.py.
pub async fn handler(
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
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
