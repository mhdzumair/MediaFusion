use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    middleware::Next,
    response::{IntoResponse, Json, Response},
};
use fred::interfaces::KeysInterface;
use md5;
use serde_json::json;

use crate::{providers::validator, state::AppState};

const DEFAULT_LIMIT: i64 = 50;
const DEFAULT_WINDOW: i64 = 60;

fn is_exempt(path: &str) -> bool {
    const EXEMPT: &[&str] = &[
        "/health",
        "/ready",
        "/static/",
        "/app/",
        "/api/v1/metrics",
        "/metrics",
        "/streaming_provider/",
        "/favicon.ico",
    ];
    EXEMPT.iter().any(|p| path.starts_with(p))
}

fn rate_limit_for_path(path: &str) -> (i64, i64, &'static str) {
    if path.contains("/kodi/stream/") {
        return (20, 3600, "kodi_stream");
    }
    if path.contains("/stream/") {
        return (20, 3600, "stream");
    }
    if path.contains("/catalog/") {
        return (150, 300, "catalog");
    }
    if path.starts_with("/encrypt-user-data") {
        return (30, 300, "user_data");
    }
    (DEFAULT_LIMIT, DEFAULT_WINDOW, "default")
}

fn rate_limit_identifier(ip: &str, secret_segment: Option<&str>) -> String {
    let mut raw = ip.to_string();
    if let Some(seg) = secret_segment.filter(|s| !s.is_empty()) {
        raw.push('-');
        raw.push_str(seg);
    }
    format!("{:x}", md5::compute(raw.as_bytes()))
}

fn secret_segment_from_path(path: &str) -> Option<&str> {
    path.trim_start_matches('/')
        .split('/')
        .next()
        .filter(|seg| {
            seg.starts_with("D-")
                || seg.starts_with("U-")
                || seg.starts_with("P-")
                || seg.len() > 20
        })
}

async fn check_rate_limit(
    redis: &fred::clients::Client,
    key: &str,
    limit: i64,
    window: i64,
) -> bool {
    match redis.incr::<i64, _>(key).await {
        Ok(count) => {
            if count == 1 {
                let _: Result<(), _> = redis.expire(key, window, None).await;
            }
            count <= limit
        }
        Err(e) => {
            tracing::warn!("rate limit redis error: {e}");
            true
        }
    }
}

/// Redis-backed sliding-window rate limiter for public instances (Python parity).
pub async fn rate_limit_middleware(
    State(state): State<Arc<AppState>>,
    req: axum::http::Request<axum::body::Body>,
    next: Next,
) -> Response {
    if !state.config.enable_rate_limit || !state.config.is_public_instance {
        return next.run(req).await;
    }

    let path = req.uri().path();
    if is_exempt(path) {
        return next.run(req).await;
    }

    let ip =
        validator::client_ip_from_headers(req.headers()).unwrap_or_else(|| "unknown".to_string());
    let (limit, window, scope) = rate_limit_for_path(path);
    let identifier = rate_limit_identifier(&ip, secret_segment_from_path(path));
    let key = format!("rate_limit:{identifier}:{scope}");

    if !check_rate_limit(&state.redis, &key, limit, window).await {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            Json(json!({
                "error": true,
                "detail": "Rate limit exceeded",
                "status_code": 429,
            })),
        )
            .into_response();
    }

    next.run(req).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stream_paths_use_stream_scope() {
        let (limit, window, scope) = rate_limit_for_path("/D-abc/stream/movie/tt123.json");
        assert_eq!(limit, 20);
        assert_eq!(window, 3600);
        assert_eq!(scope, "stream");
    }

    #[test]
    fn catalog_paths_use_catalog_scope() {
        let (_, window, scope) = rate_limit_for_path("/U-uuid/catalog/movie/top.json");
        assert_eq!(window, 300);
        assert_eq!(scope, "catalog");
    }

    #[test]
    fn playback_paths_are_exempt() {
        assert!(is_exempt(
            "/streaming_provider/D-abc/playback/infohash/season/1/episode/1"
        ));
    }
}
