use mediafusion_api::{
    config::AppConfig,
    exception_tracker, routes,
    state::{load_keyword_filter_cache, sync_keywords_from_file, AppState},
};
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

    mediafusion_api::migrate::preflight(&config.postgres_uri)
        .await
        .expect("database preflight failed");

    // One-shot migration commands: set MEDIAFUSION_MIGRATE=status or
    // MEDIAFUSION_MIGRATE_ROLLBACK_TO=<version>, then the binary runs the
    // command and exits without starting the server.
    if let Ok(cmd) = std::env::var("MEDIAFUSION_MIGRATE") {
        let uri = mediafusion_api::migrate::normalize_uri(&config.postgres_uri);
        let pool = sqlx::PgPool::connect(&uri)
            .await
            .expect("failed to connect to database");
        match cmd.trim() {
            "status" => mediafusion_api::migrate::status(&pool)
                .await
                .expect("migration status failed"),
            other => panic!("unknown MEDIAFUSION_MIGRATE value '{other}'; expected 'status'"),
        }
        return;
    }
    if let Ok(target) = std::env::var("MEDIAFUSION_MIGRATE_ROLLBACK_TO") {
        let version: i64 = target
            .trim()
            .parse()
            .expect("MEDIAFUSION_MIGRATE_ROLLBACK_TO must be an integer version number");
        let uri = mediafusion_api::migrate::normalize_uri(&config.postgres_uri);
        let pool = sqlx::PgPool::connect(&uri)
            .await
            .expect("failed to connect to database");
        mediafusion_api::migrate::rollback(&pool, version)
            .await
            .expect("migration rollback failed");
        info!(version, "migration rollback complete — exiting");
        return;
    }

    let state = AppState::build(config)
        .await
        .expect("failed to build AppState");

    mediafusion_api::migrate::run(&state.pool)
        .await
        .expect("database migration failed");

    // Sync keyword file → DB now that migrations (including 0007) are applied,
    // then refresh the in-memory cache that was loaded before migrations ran.
    sync_keywords_from_file(&state.pool).await;
    *state.keyword_filters.write().unwrap() = load_keyword_filter_cache(&state.pool).await;

    // Start the exception tracker background worker now that Redis is ready
    if let Some((rx, ttl, max_entries)) = exc_rx {
        tokio::spawn(exception_tracker::run_worker(
            state.redis.clone(),
            rx,
            ttl,
            max_entries,
        ));
    }

    let app = routes::router(state);

    let addr = format!("0.0.0.0:{port}");
    info!("mediafusion-api listening on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
