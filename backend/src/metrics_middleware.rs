use std::sync::Arc;
use std::sync::OnceLock;
use std::sync::atomic::Ordering;

use axum::{extract::State, middleware::Next, response::Response};
use fred::prelude::{
    HashesInterface, HyperloglogInterface, KeysInterface, ListInterface, SortedSetsInterface,
};
use regex::Regex;
use sha2::{Digest, Sha256};

fn to_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0xf) as usize] as char);
    }
    s
}

use crate::state::AppState;

// ─── Redis key constants ─────────────────────────────────────────────────────

/// Common prefix that separates Rust-server metrics from the Python-server
/// metrics (`req_metrics:*`) when both are pointed at the same Redis.
pub const KEY_PREFIX: &str = "req_metrics_rs:";

pub const AGG_PREFIX: &str = "req_metrics_rs:agg:";
pub const ENDPOINTS_KEY: &str = "req_metrics_rs:endpoints";
pub const RECENT_KEY: &str = "req_metrics_rs:recent";

/// Global HyperLogLog — approximate unique visitors across all endpoints.
pub const UV_GLOBAL_KEY: &str = "req_metrics_rs:uv:global";
/// Per-endpoint HyperLogLog prefix; append `{method}:{route}`.
pub const UV_PREFIX: &str = "req_metrics_rs:uv:";

/// Per-endpoint latency sorted-set prefix; append `{method}:{route}`.
/// Members are `"{duration_s:.6}:{uuid_suffix}"`, score = Unix timestamp.
pub const LATENCY_PREFIX: &str = "req_metrics_rs:latency:";

/// Time-series hashes: field = minute-aligned Unix timestamp (as string),
/// value = accumulated count / duration / error count.
pub const TS_COUNT_KEY: &str = "req_metrics_rs:ts:count";
pub const TS_TIME_KEY: &str = "req_metrics_rs:ts:time";
pub const TS_ERR_KEY: &str = "req_metrics_rs:ts:err";
/// Prefix for per-status-class time-series; append "2", "3", "4", or "5".
pub const TS_STATUS_PREFIX: &str = "req_metrics_rs:ts:s";

const METRICS_TTL: i64 = 86_400;
const RECENT_TTL: i64 = 3_600;
const MAX_RECENT: i64 = 10_000;
/// Maximum latency samples kept per endpoint (oldest evicted beyond this window).
const LATENCY_WINDOW: i64 = 1_000;

const SKIP_PREFIXES: &[&str] = &[
    "/health",
    "/ready",
    "/static",
    "/app/assets",
    "/favicon.ico",
];

// ─── Route-normalisation regexes ──────────────────────────────────────────────

static RE_UUID: OnceLock<Regex> = OnceLock::new();
static RE_NUM: OnceLock<Regex> = OnceLock::new();
static RE_JSON_ID: OnceLock<Regex> = OnceLock::new();
static RE_CONTENT_ID: OnceLock<Regex> = OnceLock::new();

fn uuid_re() -> &'static Regex {
    RE_UUID.get_or_init(|| {
        Regex::new(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
            .expect("valid UUID regex")
    })
}

fn num_re() -> &'static Regex {
    RE_NUM.get_or_init(|| Regex::new(r"/(\d+)(/|$)").expect("valid numeric segment regex"))
}

fn json_id_re() -> &'static Regex {
    RE_JSON_ID.get_or_init(|| {
        // Any .json segment whose base name contains at least one digit.
        // Covers: tt28297850.json, tt0264235%3A5%3A1.json, mdblist_movie_2406.json, 93276.json
        Regex::new(r"/[^/]*\d[^/]*\.json").expect("valid json-id regex")
    })
}

fn content_id_re() -> &'static Regex {
    RE_CONTENT_ID.get_or_init(|| {
        // tt-style IMDb IDs with any trailing extension or suffix (e.g. poster .jpg paths).
        // json_id_re already handles the .json case so this catches bare and non-json variants.
        Regex::new(r"/tt\d[^/]*").expect("valid content-id regex")
    })
}

/// Collapse dynamic path segments to `{id}` so every unique IMDb ID, episode key,
/// catalog slug, or UUID maps to the same route bucket.
///
/// Applied in order:
///   1. UUIDs            → /{id}
///   2. .json segments with a digit in the name (content API endpoints)  → /{id}
///   3. tt-style IDs with any extension (poster endpoints, etc.)          → /{id}
///   4. Pure-numeric segments                                              → /{id}
fn normalize_route(path: &str) -> String {
    let result = uuid_re().replace_all(path, "/{id}");
    let result = json_id_re().replace_all(result.as_ref(), "/{id}");
    let result = content_id_re().replace_all(result.as_ref(), "/{id}");
    // Apply twice: the trailing `/` is consumed per match, so a second pass picks up
    // the next consecutive numeric segment (e.g. `/1/3/` for season/episode).
    let result = num_re().replace_all(result.as_ref(), "/{id}$2");
    let result = num_re().replace_all(result.as_ref(), "/{id}$2");
    result.trim_end_matches('/').to_string()
}

// ─── In-flight guard ──────────────────────────────────────────────────────────

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

// ─── IP hashing ───────────────────────────────────────────────────────────────

/// Hash a client IP with a daily-rotating salt using SHA-256 so the original IP
/// is never stored while HyperLogLog cardinality estimation remains accurate.
/// Mirrors Python's `_hash_ip` in `python-deprecated/utils/request_tracker.py`.
fn hash_ip(ip: &str) -> String {
    let day_salt = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let input = format!("{ip}:{day_salt}");
    let digest = Sha256::digest(input.as_bytes());
    to_hex(&digest)
}

// ─── Redis recording ──────────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
async fn record_to_redis(
    redis: fred::clients::Client,
    method: String,
    route: String,
    status: u16,
    duration_s: f64,
    client_ip: Option<String>,
    now_iso: String,
    now_ts: f64,
) {
    let endpoint_key = format!("{method}:{route}");
    let agg_key = format!("{AGG_PREFIX}{endpoint_key}");
    let bucket = (now_ts as i64 / 60) * 60;
    let bucket_str = bucket.to_string();
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

    // ── Unique visitor tracking (HyperLogLog with daily-salted hashed IP) ──
    if let Some(ip) = client_ip {
        let hashed = hash_ip(&ip);
        let _: Result<i64, _> = redis.pfadd(UV_GLOBAL_KEY, hashed.clone()).await;
        let _: Result<(), _> = redis.expire(UV_GLOBAL_KEY, METRICS_TTL, None).await;
        let uv_ep_key = format!("{UV_PREFIX}{endpoint_key}");
        let _: Result<i64, _> = redis.pfadd(&uv_ep_key, hashed).await;
        let _: Result<(), _> = redis.expire(&uv_ep_key, METRICS_TTL, None).await;
    }

    // ── Per-endpoint latency distribution (sorted set keyed by timestamp) ──
    let latency_key = format!("{LATENCY_PREFIX}{endpoint_key}");
    let latency_member = format!("{duration_s:.6}:{}", uuid::Uuid::new_v4().simple());
    let _: Result<i64, _> = redis
        .zadd(
            &latency_key,
            None,
            None,
            false,
            false,
            (now_ts, latency_member.as_str()),
        )
        .await;
    // Cap to LATENCY_WINDOW most-recent samples
    let _: Result<i64, _> = redis
        .zremrangebyrank(&latency_key, 0, -(LATENCY_WINDOW + 1))
        .await;
    let _: Result<(), _> = redis.expire(&latency_key, METRICS_TTL, None).await;

    // ── Per-minute time-series ────────────────────────────────────────────────
    let _: Result<i64, _> = redis.hincrby(TS_COUNT_KEY, &bucket_str, 1).await;
    let _: Result<f64, _> = redis
        .hincrbyfloat(TS_TIME_KEY, &bucket_str, duration_s)
        .await;
    let status_class_digit = (status / 100).to_string();
    let ts_status_key = format!("{TS_STATUS_PREFIX}{status_class_digit}");
    let _: Result<i64, _> = redis.hincrby(&ts_status_key, &bucket_str, 1).await;
    if status >= 400 {
        let _: Result<i64, _> = redis.hincrby(TS_ERR_KEY, &bucket_str, 1).await;
        let _: Result<(), _> = redis.expire(TS_ERR_KEY, METRICS_TTL, None).await;
    }
    let _: Result<(), _> = redis.expire(TS_COUNT_KEY, METRICS_TTL, None).await;
    let _: Result<(), _> = redis.expire(TS_TIME_KEY, METRICS_TTL, None).await;
    let _: Result<(), _> = redis.expire(&ts_status_key, METRICS_TTL, None).await;

    // ── Recent requests list ──────────────────────────────────────────────────
    let request_id = uuid::Uuid::new_v4().simple().to_string();
    let entry = serde_json::json!({
        "request_id": request_id,
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

    // Extract client IP before consuming the request.
    let client_ip = crate::providers::validator::client_ip_from_headers(req.headers());

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

    // The in-process HashMap feeds only the Prometheus /metrics endpoint.
    // Skip populating it when Prometheus is disabled to avoid unbounded memory growth
    // in long-running pods that never scrape the endpoint.
    if state.config.enable_prometheus_metrics {
        state
            .metrics
            .record_request(&method, &route, status, duration_ms);
    }

    // Skip noisy health/asset paths for Redis metrics
    if !SKIP_PREFIXES.iter().any(|p| raw_path.starts_with(p)) {
        let redis = state.redis.clone();
        let now = chrono::Utc::now();
        let now_iso = now.to_rfc3339();
        let now_ts = now.timestamp() as f64;
        let duration_s = duration_ms / 1000.0;
        tokio::spawn(record_to_redis(
            redis, method, route, status, duration_s, client_ip, now_iso, now_ts,
        ));
    }

    resp
}

#[cfg(test)]
mod tests {
    use super::normalize_route;

    #[test]
    fn normalize_route_covers_all_id_patterns() {
        let cases = [
            // IMDb movie/series IDs with .json
            ("/stream/movie/tt28297850.json", "/stream/movie/{id}"),
            ("/meta/movie/tt1757678.json", "/meta/movie/{id}"),
            ("/meta/series/tt35050741.json", "/meta/series/{id}"),
            // URL-encoded episode IDs (tt<id>%3A<s>%3A<e>)
            (
                "/stream/series/tt0264235%3A5%3A1.json",
                "/stream/series/{id}",
            ),
            (
                "/stream/series/tt32767294%3A1%3A3.json",
                "/stream/series/{id}",
            ),
            // Numeric-only content IDs (TMDB etc.)
            ("/meta/series/93276.json", "/meta/series/{id}"),
            // Mixed catalog slugs (provider_name_NNNN)
            (
                "/catalog/movie/mdblist_movie_2406.json",
                "/catalog/movie/{id}",
            ),
            // Poster paths — .jpg extension consumed along with the tt ID segment
            ("/poster/movie/tt28245067.jpg", "/poster/movie/{id}"),
            // Masked secret prefix preserved
            (
                "/*MASKED*/stream/movie/tt26443616.json",
                "/*MASKED*/stream/movie/{id}",
            ),
            // Playback path: masked segments + numeric season/episode
            (
                "/streaming_provider/*MASKED*/playback/torbox/*MASKED*/1/3/*MASKED*",
                "/streaming_provider/*MASKED*/playback/torbox/*MASKED*/{id}/{id}/*MASKED*",
            ),
            // Static routes must NOT be changed
            ("/manifest.json", "/manifest.json"),
            ("/configure", "/configure"),
            ("/health", "/health"),
        ];

        for (input, expected) in &cases {
            assert_eq!(
                normalize_route(input),
                *expected,
                "normalize_route({input:?})"
            );
        }
    }
}
