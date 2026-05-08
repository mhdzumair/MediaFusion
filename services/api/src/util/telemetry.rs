pub fn init() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "mediafusion_api=info,tower_http=info".parse().unwrap()),
        )
        .with_target(false)
        .compact()
        .init();
}

/// Strip the D-prefixed secret_str token from the path so it never appears in logs.
/// e.g. /D-abc123.../manifest.json  →  /[token]/manifest.json
pub fn sanitize_path(path: &str) -> String {
    let mut parts = path.splitn(3, '/');
    parts.next(); // leading empty string before first '/'
    let first = parts.next().unwrap_or("");
    let rest = parts.next().unwrap_or("");

    if first.starts_with("D-") && first.len() > 10 {
        if rest.is_empty() {
            "/[token]".to_string()
        } else {
            format!("/[token]/{rest}")
        }
    } else {
        path.to_string()
    }
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
