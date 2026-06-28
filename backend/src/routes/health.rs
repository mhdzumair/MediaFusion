use std::sync::Arc;

use axum::{
    Json,
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Redirect},
};
use fred::prelude::ClientLike;
use serde_json::json;

use crate::state::AppState;

/// Liveness probe — returns 200 as long as the process is alive.
pub async fn handler() -> impl IntoResponse {
    Json(json!({"status": "ok"}))
}

/// Redirects browsers to the configured logo URL (matches Python `GET /favicon.ico`).
pub async fn favicon_handler(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Redirect::to(&state.config.logo_url)
}

/// Readiness probe — verifies DB and Redis are reachable (Python `core.ready`).
pub async fn ready_handler(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let mut checks = serde_json::Map::new();
    let mut healthy = true;

    match state.redis.ping::<String>(None).await {
        Ok(_) => {
            checks.insert("redis".into(), "ok".into());
        }
        Err(e) => {
            tracing::warn!("Readiness check: Redis unavailable: {e}");
            checks.insert("redis".into(), "unavailable".into());
            healthy = false;
        }
    }

    match sqlx::query("SELECT 1").fetch_one(&state.pool_ro).await {
        Ok(_) => {
            checks.insert("postgres".into(), "ok".into());
        }
        Err(e) => {
            tracing::warn!("Readiness check: Postgres unavailable: {e}");
            checks.insert("postgres".into(), "unavailable".into());
            healthy = false;
        }
    }

    let status = if healthy {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };

    (
        status,
        Json(json!({
            "status": if healthy { "ok" } else { "degraded" },
            "checks": checks,
        })),
    )
}
