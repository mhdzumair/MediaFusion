#[cfg(all(not(target_env = "msvc"), feature = "jemalloc"))]
use tikv_jemallocator::Jemalloc;

#[cfg(all(not(target_env = "msvc"), feature = "jemalloc"))]
#[global_allocator]
static ALLOC: Jemalloc = Jemalloc;

use std::sync::Arc;

use mediafusion_api::{
    config::AppConfig,
    jobs::{JobRegistry, metrics::JobMetrics},
    state::{
        AppState, load_keyword_filter_cache, maybe_recompute_keyword_blocked,
        sync_keywords_from_file,
    },
};
use tokio_util::sync::CancellationToken;
use tracing::info;

// ─── CLI helpers ──────────────────────────────────────────────────────────────

struct CliArgs {
    /// `--run-job <queue>` — run a single job inline and exit.
    run_job: Option<String>,
    /// `--args <json>` — JSON payload for --run-job (default: null).
    args: serde_json::Value,
    /// `--list-jobs` — print all registered queue names and exit.
    list_jobs: bool,
}

fn parse_args() -> CliArgs {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let mut run_job = None;
    let mut args = serde_json::Value::Null;
    let mut list_jobs = false;
    let mut i = 0;

    while i < raw.len() {
        match raw[i].as_str() {
            "--list-jobs" => {
                list_jobs = true;
            }
            "--run-job" => {
                i += 1;
                run_job = raw.get(i).cloned();
            }
            "--args" => {
                i += 1;
                if let Some(s) = raw.get(i) {
                    args = serde_json::from_str(s).unwrap_or_else(|e| {
                        eprintln!("--args: invalid JSON — {e}");
                        std::process::exit(1);
                    });
                }
            }
            other => {
                eprintln!("unknown argument '{other}'");
                eprintln!(
                    "usage: mediafusion-worker [--run-job <queue>] [--args <json>] [--list-jobs]"
                );
                std::process::exit(1);
            }
        }
        i += 1;
    }

    CliArgs {
        run_job,
        args,
        list_jobs,
    }
}

#[tokio::main]
async fn main() {
    let cli = parse_args();

    if cli.list_jobs {
        let mut reg = JobRegistry::new();
        reg.register_all_handlers();
        reg.list_queues();
        return;
    }

    let config = AppConfig::from_env();

    mediafusion_api::util::telemetry::init(None);

    mediafusion_api::migrate::preflight(&config.postgres_uri)
        .await
        .expect("database preflight failed");

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
        tracing::info!(version, "migration rollback complete — exiting");
        return;
    }

    let state = AppState::build(config)
        .await
        .expect("failed to build AppState");

    mediafusion_api::migrate::run(&state.pool)
        .await
        .expect("database migration failed");

    sync_keywords_from_file(&state.pool).await;
    *state.keyword_filters.write().unwrap() = load_keyword_filter_cache(&state.pool).await;

    {
        let kf = state.keyword_filters.read().unwrap().clone();
        maybe_recompute_keyword_blocked(&state.pool, &kf).await;
    }

    mediafusion_api::bot::register_notification_handlers(Arc::clone(&state));
    mediafusion_api::util::trackers::init_best_trackers(&state).await;

    let cancel = CancellationToken::new();

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
            Some(cancel.clone()),
        ));
    }

    // Graceful shutdown on SIGTERM / SIGINT
    {
        let cancel = cancel.clone();
        tokio::spawn(async move {
            tokio::signal::ctrl_c().await.ok();
            info!("shutdown signal received");
            cancel.cancel();
        });
    }

    let mut metrics_registry = prometheus_client::registry::Registry::default();
    let job_metrics = Arc::new(JobMetrics::new(&mut metrics_registry));

    JobMetrics::start_depth_poller(
        Arc::clone(&job_metrics),
        Arc::new(state.pool.clone()),
        cancel.clone(),
    );

    let mut reg = JobRegistry::with_state(Arc::clone(&state));
    reg.register_all_handlers();

    if let Some(ref queue) = cli.run_job {
        info!(queue, args = %cli.args, "running job once (inline)");
        let run_cancel = cancel.clone();
        {
            let run_cancel = run_cancel.clone();
            tokio::spawn(async move {
                tokio::signal::ctrl_c().await.ok();
                info!("inline job cancelled via Ctrl+C");
                run_cancel.cancel();
            });
        }
        match reg.run_once(queue, cli.args, run_cancel).await {
            Ok(()) => info!(queue, "job completed successfully"),
            Err(e) => {
                eprintln!("job failed: {e}");
                tracing::error!(queue, error = %e, "inline job failed");
                std::process::exit(1);
            }
        }
        return;
    }

    // ── Normal worker mode ────────────────────────────────────────────────────

    info!("mediafusion-worker starting");
    reg.start(job_metrics, cancel).await;
    info!("mediafusion-worker stopped");
}
