use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    middleware::Next,
    response::{IntoResponse, Json, Response},
};
use serde_json::json;

use crate::{crypto, models::user_data::UserData, state::AppState};

// Stremio user routes that appear as the second path segment after /{secret_str}/
const STREMIO_USER_ROUTE_PREFIXES: &[&str] = &[
    "meta",
    "catalog",
    "stream",
    "manifest.json",
    "configure",
    "kodi",
];

/// Middleware for all Stremio `/{secret_str}/...` routes.
///
/// Rules:
/// - `U-{uuid}`: logged-in user, skip api_password check entirely.
/// - `D-{data}`: anonymous encrypted config, validate api_password when instance
///   has one configured.
/// - No secret_str (public routes): always pass through.
/// - Unknown prefix on a Stremio user route: rejected immediately with 400.
///
/// For stream paths the error is returned as a Stremio stream object so Stremio
/// shows a meaningful message. All other Stremio paths get a 401/400.
pub async fn stremio_auth_middleware(
    State(state): State<Arc<AppState>>,
    req: axum::extract::Request,
    next: Next,
) -> Response {
    let path = req.uri().path();

    // Extract first path segment as the potential secret_str
    let first_seg = path.trim_start_matches('/').split('/').next().unwrap_or("");

    // Reject unrecognized secret formats before doing any real work.
    // Check applies regardless of public/private instance — a malformed secret is always invalid.
    if !first_seg.is_empty() && !first_seg.starts_with("D-") && !first_seg.starts_with("U-") {
        let second_seg = path
            .trim_start_matches('/')
            .split_once('/')
            .map(|x| x.1.split('/').next().unwrap_or(""))
            .unwrap_or("");
        if STREMIO_USER_ROUTE_PREFIXES
            .iter()
            .any(|r| second_seg == *r)
        {
            return invalid_secret_response(&state, path);
        }
    }

    // Public instance — no api_password enforcement
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

fn invalid_secret_response(state: &AppState, path: &str) -> Response {
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
                "description": "Invalid MediaFusion configuration.\nDelete and reconfigure the addon.",
                "url": error_video,
                "behaviorHints": { "notWebReady": true }
            }]
        }))
        .into_response()
    } else {
        (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Invalid user data. Unrecognized configuration format."})),
        )
            .into_response()
    }
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
