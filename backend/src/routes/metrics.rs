/// Prometheus metrics endpoint.
///
/// Route: GET /api/v1/metrics
///
/// Exposes live DB counts (torrents, metadata) and HTTP request metrics
/// in Prometheus text format.
///
/// Enabled only when ENABLE_PROMETHEUS_METRICS=true.
/// When PROMETHEUS_METRICS_TOKEN is set the request must carry
/// `Authorization: Bearer <token>` — this applies even on public instances,
/// so Prometheus can be configured with `bearer_token` in scrape_configs.
use std::sync::Arc;

use axum::{
    body::Body,
    extract::State,
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};
use prometheus_client::{encoding::text::encode, metrics::gauge::Gauge, registry::Registry};
use std::sync::atomic::AtomicU64;

use crate::state::AppState;

pub async fn handler(
    State(state): State<Arc<AppState>>,
    req: axum::extract::Request,
) -> Response {
    if let Some(ref required) = state.config.metrics_api_key {
        let provided = req
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "))
            .unwrap_or("");
        if provided != required.as_str() {
            return StatusCode::UNAUTHORIZED.into_response();
        }
    }
    let mut registry = Registry::default();

    // ── DB gauges ────────────────────────────────────────────────────────────
    let torrent_count: Gauge<f64, AtomicU64> = Gauge::default();
    let movie_count: Gauge<f64, AtomicU64> = Gauge::default();
    let series_count: Gauge<f64, AtomicU64> = Gauge::default();
    let usenet_count: Gauge<f64, AtomicU64> = Gauge::default();
    let telegram_count: Gauge<f64, AtomicU64> = Gauge::default();

    registry.register(
        "mediafusion_torrents_total",
        "Total number of torrent streams",
        torrent_count.clone(),
    );
    registry.register(
        "mediafusion_movies_total",
        "Total number of movie metadata entries",
        movie_count.clone(),
    );
    registry.register(
        "mediafusion_series_total",
        "Total number of series metadata entries",
        series_count.clone(),
    );
    registry.register(
        "mediafusion_usenet_streams_total",
        "Total number of usenet streams",
        usenet_count.clone(),
    );
    registry.register(
        "mediafusion_telegram_streams_total",
        "Total number of telegram streams",
        telegram_count.clone(),
    );

    // Fetch counts concurrently.
    // Large stream tables use pg_stat_user_tables estimates (fast catalog lookup)
    // instead of COUNT(*) full-table scans — avoids holding DB connections during
    // high-load Prometheus scrapes. Media counts use exact COUNT with the type index.
    let (tc, mc, sc, uc, tgc) = tokio::join!(
        fetch_estimate(&state.pool_ro, "torrent_stream"),
        fetch_count(
            &state.pool_ro,
            "SELECT COUNT(*) FROM media WHERE type = 'MOVIE'"
        ),
        fetch_count(
            &state.pool_ro,
            "SELECT COUNT(*) FROM media WHERE type = 'SERIES'"
        ),
        fetch_estimate(&state.pool_ro, "usenet_stream"),
        fetch_estimate(&state.pool_ro, "telegram_stream"),
    );

    torrent_count.set(tc as f64);
    movie_count.set(mc as f64);
    series_count.set(sc as f64);
    usenet_count.set(uc as f64);
    telegram_count.set(tgc as f64);

    // ── Encode prometheus registry ────────────────────────────────────────────
    let mut buf = String::new();
    if let Err(e) = encode(&mut buf, &registry) {
        tracing::error!("metrics encode error: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    // ── HTTP request metrics (manual Prometheus text format) ─────────────────
    buf.push_str("# HELP http_requests_total Total HTTP requests\n");
    buf.push_str("# TYPE http_requests_total counter\n");

    if let Ok(requests) = state.metrics.requests.read() {
        let mut entries: Vec<_> = requests.iter().collect();
        entries.sort_by(|a, b| a.0.cmp(b.0));
        for ((method, route, status), count) in &entries {
            buf.push_str(&format!(
                "http_requests_total{{method=\"{method}\",route=\"{route}\",status=\"{status}\"}} {count}\n"
            ));
        }
    }

    buf.push_str(
        "# HELP http_request_duration_ms_sum Sum of HTTP request durations in milliseconds\n",
    );
    buf.push_str("# TYPE http_request_duration_ms_sum gauge\n");
    buf.push_str(
        "# HELP http_request_duration_ms_count Count of HTTP requests for duration tracking\n",
    );
    buf.push_str("# TYPE http_request_duration_ms_count gauge\n");

    if let Ok(durations) = state.metrics.durations.read() {
        let mut entries: Vec<_> = durations.iter().collect();
        entries.sort_by(|a, b| a.0.cmp(b.0));
        for ((method, route, status), (sum_ms, count)) in &entries {
            buf.push_str(&format!(
                "http_request_duration_ms_sum{{method=\"{method}\",route=\"{route}\",status=\"{status}\"}} {sum_ms:.3}\n"
            ));
            buf.push_str(&format!(
                "http_request_duration_ms_count{{method=\"{method}\",route=\"{route}\",status=\"{status}\"}} {count}\n"
            ));
        }
    }

    buf.push_str("# HELP http_in_flight_requests Current number of in-flight HTTP requests\n");
    buf.push_str("# TYPE http_in_flight_requests gauge\n");
    buf.push_str(&format!(
        "http_in_flight_requests {}\n",
        state.metrics.in_flight_count()
    ));

    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/plain; version=0.0.4")
        .body(Body::from(buf))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

async fn fetch_count(pool: &sqlx::PgPool, query: &str) -> i64 {
    sqlx::query_scalar::<_, i64>(query)
        .fetch_one(pool)
        .await
        .unwrap_or(0)
}

async fn fetch_estimate(pool: &sqlx::PgPool, table: &str) -> i64 {
    sqlx::query_scalar::<_, i64>(
        "SELECT GREATEST(n_live_tup, 0) FROM pg_stat_user_tables WHERE relname = $1",
    )
    .bind(table)
    .fetch_one(pool)
    .await
    .unwrap_or(0)
}
