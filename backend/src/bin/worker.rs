#[cfg(all(not(target_env = "msvc"), feature = "jemalloc"))]
use tikv_jemallocator::Jemalloc;

#[cfg(all(not(target_env = "msvc"), feature = "jemalloc"))]
#[global_allocator]
static ALLOC: Jemalloc = Jemalloc;

use std::sync::Arc;

use mediafusion_api::{
    config::AppConfig,
    jobs::{
        handlers::{
            acestream_bg::AcestreamBgScraper,
            backfill_stream_metadata::BackfillStreamMetadata,
            background_search::BackgroundSearch,
            cleanup::Cleanup,
            discover_prewarm::DiscoverPrewarm,
            dmm_hashlist::DmmHashlistScraper,
            imdb_dataset_import::ImdbDatasetImport,
            integration_syncs::IntegrationSyncs,
            jackett_feed::JackettFeedScraper,
            m3u_import::M3uImport,
            pending_moderation_reminder::PendingModerationReminder,
            prowlarr_feed::ProwlarrFeedScraper,
            rss_feed::RssFeedScraper,
            spiders::{
                arab_torrents::ArabTorrentsCrawl,
                ext_to::{
                    FormulaExtCrawl, MotogpExtCrawl, MoviesExtCrawl, UfcExtCrawl, WweExtCrawl,
                },
                eztv_rss::EztvRssCrawl,
                registry_crawl::RegistryCrawl,
                sport_video::SportVideoCrawl,
                tamil_forums::{TamilBlastersCrawl, TamilMvCrawl},
            },
            telegram_bg::TelegramBgScraper,
            update_seeders::UpdateSeeders,
            update_tv_posters::UpdateTvPosters,
            validate_tv::ValidateTvStreams,
            xtream_import::XtreamImport,
            youtube_bg::YoutubeBgScraper,
        },
        metrics::JobMetrics,
        JobRegistry,
    },
    state::{
        load_keyword_filter_cache, maybe_recompute_keyword_blocked, sync_keywords_from_file,
        AppState,
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

    let mut reg = JobRegistry::new(Arc::clone(&state));

    // Non-Scrapy background tasks
    reg.register(Arc::new(BackgroundSearch));
    reg.register(Arc::new(ProwlarrFeedScraper));
    reg.register(Arc::new(JackettFeedScraper));
    reg.register(Arc::new(RssFeedScraper));
    reg.register(Arc::new(DmmHashlistScraper));
    reg.register(Arc::new(YoutubeBgScraper));
    reg.register(Arc::new(AcestreamBgScraper));
    reg.register(Arc::new(TelegramBgScraper));
    reg.register(Arc::new(BackfillStreamMetadata));
    reg.register(Arc::new(ValidateTvStreams));
    reg.register(Arc::new(UpdateSeeders));
    reg.register(Arc::new(UpdateTvPosters));
    reg.register(Arc::new(DiscoverPrewarm));
    reg.register(Arc::new(ImdbDatasetImport));
    reg.register(Arc::new(Cleanup));
    reg.register(Arc::new(IntegrationSyncs));
    reg.register(Arc::new(PendingModerationReminder));
    reg.register(Arc::new(M3uImport));
    reg.register(Arc::new(XtreamImport));

    // Spider handlers
    reg.register(Arc::new(EztvRssCrawl));
    reg.register(Arc::new(RegistryCrawl)); // covers bt4g, nyaa, animetosho, subsplease, animepahe, bt52, uindex, x1337, thepiratebay, rutor, limetorrents, yts
    reg.register(Arc::new(TamilMvCrawl));
    reg.register(Arc::new(TamilBlastersCrawl));
    reg.register(Arc::new(FormulaExtCrawl));
    reg.register(Arc::new(MotogpExtCrawl));
    reg.register(Arc::new(WweExtCrawl));
    reg.register(Arc::new(UfcExtCrawl));
    reg.register(Arc::new(MoviesExtCrawl));
    reg.register(Arc::new(SportVideoCrawl));
    reg.register(Arc::new(ArabTorrentsCrawl));

    // ── CLI one-shot modes ────────────────────────────────────────────────────

    if cli.list_jobs {
        reg.list_queues();
        return;
    }

    if let Some(ref queue) = cli.run_job {
        info!(queue, args = %cli.args, "running job once (inline)");
        match reg.run_once(queue, cli.args, cancel).await {
            Ok(()) => info!(queue, "job completed successfully"),
            Err(e) => {
                tracing::error!(queue, "{e}");
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
