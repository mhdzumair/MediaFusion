#[cfg(all(not(target_env = "msvc"), feature = "jemalloc"))]
use tikv_jemallocator::Jemalloc;

#[cfg(all(not(target_env = "msvc"), feature = "jemalloc"))]
#[global_allocator]
static ALLOC: Jemalloc = Jemalloc;

use std::sync::Arc;

use mediafusion_api::{
    config::AppConfig,
    exception_tracker, routes,
    state::{
        load_keyword_filter_cache, maybe_recompute_keyword_blocked,
        maybe_recompute_stream_keyword_blocked, sync_keywords_from_file, AppState,
    },
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

    // Sync keyword file → DB and recompute blocked flags in the background so the
    // server starts accepting requests immediately.
    {
        let pool = state.pool.clone();
        let kf_lock = Arc::clone(&state.keyword_filters);
        tokio::spawn(async move {
            sync_keywords_from_file(&pool).await;
            let kf = load_keyword_filter_cache(&pool).await;
            maybe_recompute_keyword_blocked(&pool, &kf).await;
            maybe_recompute_stream_keyword_blocked(&pool, &kf).await;
            *kf_lock.write().unwrap() = kf;
        });
    }

    // Start the exception tracker background worker now that Redis is ready
    if let Some((rx, ttl, max_entries)) = exc_rx {
        tokio::spawn(exception_tracker::run_worker(
            state.redis.clone(),
            rx,
            ttl,
            max_entries,
        ));
    }

    if state.config.telegram_bot_token.is_some() {
        let bot_state = Arc::clone(&state);
        tokio::spawn(async move {
            mediafusion_api::bot::register_commands(bot_state).await;
        });
    }

    mediafusion_api::bot::register_notification_handlers(Arc::clone(&state));
    {
        let tracker_state = Arc::clone(&state);
        tokio::spawn(async move {
            mediafusion_api::util::trackers::init_best_trackers(&tracker_state).await;
        });
    }

    if state.config.egress_watchdog_enabled {
        let watchdog_http = state.http.clone();
        let watchdog_cfg = mediafusion_api::util::egress_watchdog::WatchdogConfig {
            interval_secs: state.config.egress_watchdog_interval_secs,
            fail_threshold: state.config.egress_watchdog_fail_threshold,
            probe_urls_override: state.config.egress_watchdog_probe_urls.clone(),
        };
        tokio::spawn(mediafusion_api::util::egress_watchdog::run(
            watchdog_http,
            watchdog_cfg,
            None,
        ));
    }

    let app = routes::router(state);

    let addr = format!("0.0.0.0:{port}");
    info!("mediafusion-api listening on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
