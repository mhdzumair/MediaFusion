use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    middleware::Next,
    response::{IntoResponse, Json, Response},
};
use serde_json::json;

use crate::{crypto, models::user_data::UserData, state::AppState};

/// Middleware for all Stremio `/{secret_str}/...` routes.
///
/// Rules:
/// - `U-{uuid}`: logged-in user, skip api_password check entirely.
/// - `D-{data}`: anonymous encrypted config, validate api_password when instance
///   has one configured.
/// - No secret_str (public routes): always pass through.
///
/// For stream paths the error is returned as a Stremio stream object so Stremio
/// shows a meaningful message. All other Stremio paths get a 401.
pub async fn stremio_auth_middleware(
    State(state): State<Arc<AppState>>,
    req: axum::extract::Request,
    next: Next,
) -> Response {
    let path = req.uri().path();

    // Extract first path segment as the potential secret_str
    let first_seg = path.trim_start_matches('/').split('/').next().unwrap_or("");

    // Public instance — no enforcement at all
    if state.config.is_public_instance {
        return next.run(req).await;
    }

    // Check `encoded_user_data` header: if present and instance requires api_password,
    // decode it and validate the `ap` field from the decoded JSON.
    let encoded_header = req
        .headers()
        .get("encoded_user_data")
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);

    if let Some(ref hv) = encoded_header {
        if let Some(ref required) = state.config.api_password {
            let raw = crypto::decode_encoded_user_data(hv)
                .unwrap_or_else(|| serde_json::Value::Object(Default::default()));
            let user_data: UserData = serde_json::from_value(raw).unwrap_or_default();
            let provided = user_data.api_password.as_deref().unwrap_or("");
            if provided != required.as_str() {
                return unauthorized_response(&state, path);
            }
        }
        return next.run(req).await;
    }

    // Only D- (anonymous encrypted) secrets need the api_password check.
    // U- secrets belong to logged-in users who never store api_password in their profile.
    if first_seg.starts_with("D-") {
        if let Some(ref required) = state.config.api_password {
            let raw = crypto::resolve_user_data(
                first_seg,
                &state.config.secret_key,
                &state.pool,
                &state.redis,
            )
            .await;
            let user_data: UserData = serde_json::from_value(raw).unwrap_or_default();
            let provided = user_data.api_password.as_deref().unwrap_or("");

            if provided != required.as_str() {
                return unauthorized_response(&state, path);
            }
        }
    }

    next.run(req).await
}

fn unauthorized_response(state: &AppState, path: &str) -> Response {
    // Stream endpoints: return a Stremio stream error object so the player shows a message.
    // All other Stremio endpoints (manifest, catalog, meta): return 401.
    let path_after_secret = path
        .trim_start_matches('/')
        .split_once('/')
        .map(|x| x.1)
        .unwrap_or("");

    if path_after_secret.starts_with("stream/") {
        let error_video = format!(
            "{}/static/exceptions/invalid_config.mp4",
            state.config.host_url
        );
        Json(json!({
            "streams": [{
                "name": state.config.addon_name,
                "description": "Unauthorized.\nInvalid MediaFusion configuration.\nDelete the Invalid MediaFusion installed addon and reconfigure it.",
                "url": error_video,
                "behaviorHints": { "notWebReady": true }
            }]
        }))
        .into_response()
    } else {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "Unauthorized. Invalid or missing API password."})),
        )
            .into_response()
    }
}
