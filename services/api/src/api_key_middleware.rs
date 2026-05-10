use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    middleware::Next,
    response::{IntoResponse, Json, Response},
};
use serde_json::json;

use crate::state::AppState;

/// Paths under /api/v1/ that do NOT require X-API-Key even on private instances.
/// Mirrors Python's APIKeyMiddleware EXEMPT_PATH_PREFIXES (api/v1 subset only).
const EXEMPT_PREFIXES: &[&str] = &[
    "/api/v1/instance/",
    "/api/v1/integrations/simkl/callback",
    "/api/v1/telegram/webhook",
    "/api/v1/telegram/login",
];

/// Enforces X-API-Key header on all /api/v1/* endpoints for private instances.
///
/// Rules (mirrors Python's APIKeyMiddleware):
/// - Public instance (is_public_instance = true): always passes through.
/// - No api_password configured: always passes through.
/// - Path does not start with /api/v1/: passes through (Stremio/health/static routes).
/// - Path matches an exempt prefix: passes through.
/// - Otherwise: X-API-Key header must equal api_password, else 401.
pub async fn api_key_middleware(
    State(state): State<Arc<AppState>>,
    req: axum::extract::Request,
    next: Next,
) -> Response {
    // Public instance — no enforcement at all
    if state.config.is_public_instance {
        return next.run(req).await;
    }

    let path = req.uri().path();

    // Only enforce on /api/v1/ paths
    if !path.starts_with("/api/v1/") {
        return next.run(req).await;
    }

    // Exempt paths
    if EXEMPT_PREFIXES.iter().any(|p| path.starts_with(p)) {
        return next.run(req).await;
    }

    // No password configured — nothing to enforce
    let Some(ref required) = state.config.api_password else {
        return next.run(req).await;
    };

    // Validate X-API-Key header
    let provided = req
        .headers()
        .get("X-API-Key")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    if provided != required.as_str() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({
                "error": true,
                "detail": "Invalid or missing API key",
                "status_code": 401
            })),
        )
            .into_response();
    }

    next.run(req).await
}
