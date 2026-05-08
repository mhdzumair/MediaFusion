use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::sync::OnceLock;

use axum::{extract::State, middleware::Next, response::Response};
use regex::Regex;

use crate::state::AppState;

static RE_UUID: OnceLock<Regex> = OnceLock::new();
static RE_NUM: OnceLock<Regex> = OnceLock::new();

fn uuid_re() -> &'static Regex {
    RE_UUID.get_or_init(|| {
        Regex::new(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
            .expect("valid UUID regex")
    })
}

fn num_re() -> &'static Regex {
    RE_NUM.get_or_init(|| Regex::new(r"/(\d+)(/|$)").expect("valid numeric segment regex"))
}

fn normalize_route(path: &str) -> String {
    let result = uuid_re().replace_all(path, "/{id}");
    let result = num_re().replace_all(result.as_ref(), "/{id}$2");
    result.trim_end_matches('/').to_string()
}

pub async fn metrics_middleware(
    State(state): State<Arc<AppState>>,
    req: axum::http::Request<axum::body::Body>,
    next: Next,
) -> Response {
    let method = req.method().to_string();
    let path = req.uri().path().to_string();
    let route = normalize_route(&path);

    state.metrics.in_flight.fetch_add(1, Ordering::Relaxed);
    let start = std::time::Instant::now();
    let resp = next.run(req).await;
    let duration_ms = start.elapsed().as_secs_f64() * 1000.0;
    let status = resp.status().as_u16();
    state.metrics.in_flight.fetch_sub(1, Ordering::Relaxed);

    state
        .metrics
        .record_request(&method, &route, status, duration_ms);
    resp
}
