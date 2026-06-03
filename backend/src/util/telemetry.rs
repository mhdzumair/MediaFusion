use crate::exception_tracker::ExceptionTrackerLayer;
use tokio::sync::mpsc;

pub fn init(exc_tx: Option<mpsc::UnboundedSender<crate::exception_tracker::ExcEvent>>) {
    use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

    let env_filter = tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| {
        "mediafusion_api=debug,tower_http=info,sqlx=warn"
            .parse()
            .unwrap()
    });

    let fmt_layer = tracing_subscriber::fmt::layer()
        .with_target(false)
        .compact();

    let exc_layer = exc_tx.map(|tx| ExceptionTrackerLayer { tx });

    tracing_subscriber::Registry::default()
        .with(env_filter)
        .with(fmt_layer)
        .with(exc_layer)
        .init();
}

/// Strip user/secret tokens from paths so they never appear in logs.
/// Masks any path segment that starts with "D-" or "U-" and is longer than 10 chars.
/// e.g. /D-abc123.../manifest.json            →  /[token]/manifest.json
///      /U-03824ebc-.../manifest.json          →  /[token]/manifest.json
///      /streaming_provider/D-abc.../playback  →  /streaming_provider/[token]/playback
pub fn sanitize_path(path: &str) -> String {
    path.split('/')
        .map(|seg| {
            if (seg.starts_with("D-") || seg.starts_with("U-") || seg.starts_with("P-"))
                && seg.len() > 10
            {
                return "*MASKED*";
            }
            // Long opaque path segments (legacy secret_str / existing_secret_str).
            if seg.len() > 24
                && seg
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
            {
                return "*MASKED*";
            }
            seg
        })
        .collect::<Vec<_>>()
        .join("/")
}

/// Return a TraceLayer configured with sanitized path logging and latency in milliseconds.
/// This macro-like helper is called in routes/mod.rs via the `make_trace_layer!()` macro below.
///
/// Because `make_span_with` changes the `MakeSpan` type parameter, the return type cannot be
/// written as a named type. Use the `make_trace_layer!` macro to inline the expression where
/// needed, keeping all tracing configuration in this module.
#[macro_export]
macro_rules! make_trace_layer {
    () => {
        tower_http::trace::TraceLayer::new_for_http()
            .make_span_with(|request: &axum::http::Request<axum::body::Body>| {
                let safe_path = $crate::util::telemetry::sanitize_path(request.uri().path());
                tracing::info_span!(
                    "http",
                    method = %request.method(),
                    path = %safe_path,
                )
            })
            .on_request(
                tower_http::trace::DefaultOnRequest::new()
                    .level(tracing::Level::INFO),
            )
            .on_response(
                tower_http::trace::DefaultOnResponse::new()
                    .level(tracing::Level::INFO)
                    .latency_unit(tower_http::LatencyUnit::Millis),
            )
            .on_failure(
                tower_http::trace::DefaultOnFailure::new()
                    .level(tracing::Level::ERROR),
            )
    };
}
