use mediafusion_api::{config::AppConfig, routes, state::AppState};
use tracing::info;

#[tokio::main]
async fn main() {
    mediafusion_api::util::telemetry::init();

    let config = AppConfig::from_env();
    let port = config.port;

    let state = AppState::build(config)
        .await
        .expect("failed to build AppState");

    let app = routes::router(state);

    let addr = format!("0.0.0.0:{port}");
    info!("mediafusion-api listening on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
