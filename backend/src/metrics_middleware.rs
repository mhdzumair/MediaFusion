use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::sync::OnceLock;

use axum::{extract::State, middleware::Next, response::Response};
use fred::prelude::{HashesInterface, KeysInterface, ListInterface, SortedSetsInterface};
use regex::Regex;

use crate::state::AppState;

pub const AGG_PREFIX: &str = "req_metrics:agg:";
pub const ENDPOINTS_KEY: &str = "req_metrics:endpoints";
pub const RECENT_KEY: &str = "req_metrics:recent";
const METRICS_TTL: i64 = 86400;
const RECENT_TTL: i64 = 3600;
const MAX_RECENT: i64 = 10000;

const SKIP_PREFIXES: &[&str] = &[
    "/health",
    "/ready",
    "/static",
    "/app/assets",
    "/favicon.ico",
];

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

async fn record_to_redis(
    redis: fred::clients::Client,
    method: String,
    route: String,
    status: u16,
    duration_s: f64,
    now_iso: String,
    now_ts: f64,
) {
    let endpoint_key = format!("{method}:{route}");
    let agg_key = format!("{AGG_PREFIX}{endpoint_key}");

    let status_class = format!("status_{}xx", status / 100);

    // Aggregate stats (atomic increments — no read-modify-write race)
    let _: Result<i64, _> = redis.hincrby(&agg_key, "total_requests", 1).await;
    let _: Result<f64, _> = redis.hincrbyfloat(&agg_key, "total_time", duration_s).await;
    let _: Result<i64, _> = redis.hincrby(&agg_key, &status_class, 1).await;
    if status >= 400 {
        let _: Result<i64, _> = redis.hincrby(&agg_key, "error_count", 1).await;
    }
    // last_seen and identity fields (non-atomic, small race acceptable for metrics)
    let mut fields = std::collections::HashMap::new();
    fields.insert("last_seen", now_iso.clone());
    let _: Result<(), _> = redis.hset(&agg_key, fields).await;
    let _: Result<bool, _> = redis.hsetnx(&agg_key, "method", &method).await;
    let _: Result<bool, _> = redis.hsetnx(&agg_key, "route", &route).await;

    // min/max time (read-then-update; approximate under concurrent writes is fine for metrics)
    let existing_min: Option<String> = redis.hget(&agg_key, "min_time").await.unwrap_or(None);
    let existing_max: Option<String> = redis.hget(&agg_key, "max_time").await.unwrap_or(None);
    let cur_min = existing_min
        .as_deref()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(f64::INFINITY);
    let cur_max = existing_max
        .as_deref()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(0.0_f64);
    if duration_s < cur_min {
        let _: Result<bool, _> = redis
            .hset(&agg_key, [("min_time", format!("{duration_s:.6}"))])
            .await;
    }
    if duration_s > cur_max {
        let _: Result<bool, _> = redis
            .hset(&agg_key, [("max_time", format!("{duration_s:.6}"))])
            .await;
    }

    let _: Result<(), _> = redis.expire(&agg_key, METRICS_TTL, None).await;

    // Endpoints sorted set — score = Unix timestamp for recency ordering
    let _: Result<i64, _> = redis
        .zadd(
            ENDPOINTS_KEY,
            None,
            None,
            false,
            false,
            (now_ts, endpoint_key.as_str()),
        )
        .await;
    let _: Result<(), _> = redis.expire(ENDPOINTS_KEY, METRICS_TTL, None).await;

    // Recent requests list
    let entry = serde_json::json!({
        "method": method,
        "path": route,
        "route_template": route,
        "status_code": status,
        "process_time": duration_s,
        "timestamp": now_iso,
    });
    if let Ok(json_str) = serde_json::to_string(&entry) {
        let _: Result<i64, _> = redis.lpush(RECENT_KEY, json_str).await;
        let _: Result<(), _> = redis.ltrim(RECENT_KEY, 0, MAX_RECENT - 1).await;
        let _: Result<(), _> = redis.expire(RECENT_KEY, RECENT_TTL, None).await;
    }
}

pub async fn metrics_middleware(
    State(state): State<Arc<AppState>>,
    req: axum::http::Request<axum::body::Body>,
    next: Next,
) -> Response {
    let method = req.method().to_string();
    let raw_path = req.uri().path().to_string();
    let path = crate::util::telemetry::sanitize_path(&raw_path);
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
    drop(_guard);

    state
        .metrics
        .record_request(&method, &route, status, duration_ms);

    // Skip noisy health/asset paths for Redis metrics
    if !SKIP_PREFIXES.iter().any(|p| raw_path.starts_with(p)) {
        let redis = state.redis.clone();
        let now = chrono::Utc::now();
        let now_iso = now.to_rfc3339();
        let now_ts = now.timestamp() as f64;
        let duration_s = duration_ms / 1000.0;
        tokio::spawn(record_to_redis(
            redis, method, route, status, duration_s, now_iso, now_ts,
        ));
    }

    resp
}
