use std::sync::Arc;

use mediafusion_api::{config::AppConfig, exception_tracker, routes, state::AppState};
use tracing::info;

#[tokio::main]
async fn main() {
    let config = AppConfig::from_env();
    let port = config.port;

    // Create the exception tracker channel before tracing init so early errors are captured
    let exc_rx = if config.enable_exception_tracking {
        let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
        mediafusion_api::util::telemetry::init(Some(tx));
        Some((
            rx,
            config.exception_tracking_ttl,
            config.exception_tracking_max_entries,
        ))
    } else {
        mediafusion_api::util::telemetry::init(None);
        None
    };

    let state = AppState::build(config)
        .await
        .expect("failed to build AppState");

    // Start the exception tracker background worker now that Redis is ready
    if let Some((rx, ttl, max_entries)) = exc_rx {
        tokio::spawn(exception_tracker::run_worker(
            state.redis.clone(),
            rx,
            ttl,
            max_entries,
        ));
    }

    // Start background scheduler (enqueues taskiq tasks via Redis Streams)
    mediafusion_api::scheduler::start(Arc::clone(&state));

    let app = routes::router(state);

    let addr = format!("0.0.0.0:{port}");
    info!("mediafusion-api listening on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
