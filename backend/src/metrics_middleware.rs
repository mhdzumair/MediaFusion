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

/// RAII guard that decrements the in-flight counter when dropped.
/// Ensures the decrement happens even when a future is cancelled (client disconnect).
struct InFlightGuard<'a> {
    counter: &'a std::sync::atomic::AtomicU64,
}

impl Drop for InFlightGuard<'_> {
    fn drop(&mut self) {
        self.counter.fetch_sub(1, Ordering::Relaxed);
    }
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
    // Guard ensures decrement on drop — covers both normal completion and future cancellation.
    let _guard = InFlightGuard {
        counter: &state.metrics.in_flight,
    };

    let start = std::time::Instant::now();
    let resp = next.run(req).await;
    let duration_ms = start.elapsed().as_secs_f64() * 1000.0;
    let status = resp.status().as_u16();
    // Guard drops here (or earlier on cancellation).
    drop(_guard);

    state
        .metrics
        .record_request(&method, &route, status, duration_ms);
    resp
}
