use std::collections::HashMap;
use std::sync::Arc;

use tokio_util::sync::CancellationToken;
use tracing::info;

use super::{
    error::JobError,
    handler::{ErasedHandler, JobCtx},
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
        poster_nsfw_scan::PosterNsfwScan,
        prowlarr_feed::ProwlarrFeedScraper,
        rss_feed::RssFeedScraper,
        spiders::{
            arab_torrents::ArabTorrentsCrawl,
            ext_to::{FormulaExtCrawl, MotogpExtCrawl, MoviesExtCrawl, UfcExtCrawl, WweExtCrawl},
            eztv_rss::EztvRssCrawl,
            fighting_feeds::FightingFeedsCrawl,
            formula_feeds::FormulaFeedsCrawl,
            movierulz::MovieRulzCrawl,
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
    runner::{
        QueueRunner, insert_inline_job, mark_job_cancelled, mark_job_failed, mark_job_success,
    },
    scheduler,
};
use crate::state::AppState;

pub struct JobRegistry {
    handlers: HashMap<&'static str, Arc<dyn ErasedHandler>>,
    state: Option<Arc<AppState>>,
}

impl Default for JobRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl JobRegistry {
    pub fn new() -> Self {
        Self {
            handlers: HashMap::new(),
            state: None,
        }
    }

    pub fn with_state(state: Arc<AppState>) -> Self {
        Self {
            handlers: HashMap::new(),
            state: Some(state),
        }
    }

    pub fn register<H: ErasedHandler>(&mut self, handler: Arc<H>) {
        let queue = handler.queue();
        info!(queue, "registered handler");
        self.handlers.insert(queue, handler);
    }

    /// Print all registered queue names to stdout (for `--list-jobs`).
    pub fn list_queues(&self) {
        let mut queues: Vec<&str> = self.handlers.keys().copied().collect();
        queues.sort_unstable();
        println!("Registered job queues:");
        for q in queues {
            println!("  {q}");
        }
    }

    /// Register every worker handler without touching PostgreSQL or Redis.
    pub fn register_all_handlers(&mut self) {
        // Non-Scrapy background tasks
        self.register(Arc::new(BackgroundSearch));
        self.register(Arc::new(ProwlarrFeedScraper));
        self.register(Arc::new(JackettFeedScraper));
        self.register(Arc::new(RssFeedScraper));
        self.register(Arc::new(DmmHashlistScraper));
        self.register(Arc::new(YoutubeBgScraper));
        self.register(Arc::new(AcestreamBgScraper));
        self.register(Arc::new(TelegramBgScraper));
        self.register(Arc::new(BackfillStreamMetadata));
        self.register(Arc::new(ValidateTvStreams));
        self.register(Arc::new(UpdateSeeders));
        self.register(Arc::new(UpdateTvPosters));
        self.register(Arc::new(PosterNsfwScan));
        self.register(Arc::new(DiscoverPrewarm));
        self.register(Arc::new(ImdbDatasetImport));
        self.register(Arc::new(Cleanup));
        self.register(Arc::new(IntegrationSyncs));
        self.register(Arc::new(PendingModerationReminder));
        self.register(Arc::new(M3uImport));
        self.register(Arc::new(XtreamImport));

        // Spider handlers
        self.register(Arc::new(EztvRssCrawl));
        self.register(Arc::new(RegistryCrawl));
        self.register(Arc::new(TamilMvCrawl));
        self.register(Arc::new(TamilBlastersCrawl));
        self.register(Arc::new(FormulaExtCrawl));
        self.register(Arc::new(FormulaFeedsCrawl));
        self.register(Arc::new(FightingFeedsCrawl));
        self.register(Arc::new(MovieRulzCrawl));
        self.register(Arc::new(MotogpExtCrawl));
        self.register(Arc::new(WweExtCrawl));
        self.register(Arc::new(UfcExtCrawl));
        self.register(Arc::new(MoviesExtCrawl));
        self.register(Arc::new(SportVideoCrawl));
        self.register(Arc::new(ArabTorrentsCrawl));
    }

    /// Run a single job inline (CLI `--run-job`), recording progress in the `jobs` table.
    /// `args` is the raw JSON payload passed to the handler.
    pub async fn run_once(
        &self,
        queue: &str,
        args: serde_json::Value,
        cancel: CancellationToken,
    ) -> Result<(), String> {
        let state = self
            .state
            .as_ref()
            .expect("JobRegistry state required to run jobs");
        let handler = self.handlers.get(queue).ok_or_else(|| {
            format!("unknown queue '{queue}' — run with --list-jobs to see options")
        })?;

        let payload = if args.is_null() {
            serde_json::json!({})
        } else {
            args
        };

        if queue == "imdb_dataset_import"
            && let Some(running_id) =
                super::handlers::imdb_dataset_import::active_import_job_id(&state.pool)
                    .await
                    .map_err(|e| format!("failed to check import status: {e}"))?
        {
            return Err(format!(
                "IMDb import already running (job #{running_id}). \
                 Wait for it to finish, or stop the worker process before starting another run."
            ));
        }

        let job_id = insert_inline_job(&state.pool, queue, &payload)
            .await
            .map_err(|e| format!("failed to record inline job: {e}"))?;

        let ctx = JobCtx {
            job_id,
            attempt: 1,
            state: Arc::clone(state),
            cancel,
        };

        match handler.run_erased(payload, ctx).await {
            Ok(()) => {
                mark_job_success(&state.pool, job_id).await;
                Ok(())
            }
            Err(JobError::Cancelled) => {
                mark_job_cancelled(&state.pool, job_id).await;
                Err("job cancelled".into())
            }
            Err(e) => {
                let msg = e.to_string();
                mark_job_failed(&state.pool, job_id, &msg).await;
                Err(format!("job failed: {msg}"))
            }
        }
    }

    /// Start all runners and the scheduler. Blocks until `cancel` fires.
    pub async fn start(self, metrics: Arc<JobMetrics>, cancel: CancellationToken) {
        let state = self
            .state
            .expect("JobRegistry state required to start worker");
        let pool = Arc::new(state.pool.clone());

        for (_queue, handler) in self.handlers {
            let runner = QueueRunner::new(
                handler,
                Arc::clone(&state),
                Arc::clone(&metrics),
                cancel.clone(),
            );
            runner.start();
        }

        let disable_all = state.config.disable_all_scheduler;
        scheduler::run(pool, cancel, disable_all).await;
    }
}
