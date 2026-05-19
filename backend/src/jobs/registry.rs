use std::collections::HashMap;
use std::sync::Arc;

use tokio_util::sync::CancellationToken;
use tracing::info;

use super::{
    handler::{ErasedHandler, JobCtx},
    metrics::JobMetrics,
    runner::QueueRunner,
    scheduler,
};
use crate::state::AppState;

pub struct JobRegistry {
    handlers: HashMap<&'static str, Arc<dyn ErasedHandler>>,
    state: Arc<AppState>,
}

impl JobRegistry {
    pub fn new(state: Arc<AppState>) -> Self {
        Self {
            handlers: HashMap::new(),
            state,
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

    /// Run a single job inline without touching the DB queue, then exit.
    /// `args` is the raw JSON payload passed to the handler.
    pub async fn run_once(
        &self,
        queue: &str,
        args: serde_json::Value,
        cancel: CancellationToken,
    ) -> Result<(), String> {
        let handler = self.handlers.get(queue).ok_or_else(|| {
            format!("unknown queue '{queue}' — run with --list-jobs to see options")
        })?;

        let ctx = JobCtx {
            job_id: -1,
            attempt: 1,
            state: Arc::clone(&self.state),
            cancel,
        };

        handler
            .run_erased(args, ctx)
            .await
            .map_err(|e| format!("job failed: {e}"))
    }

    /// Start all runners and the scheduler. Blocks until `cancel` fires.
    pub async fn start(self, metrics: Arc<JobMetrics>, cancel: CancellationToken) {
        let pool = Arc::new(self.state.pool.clone());

        for (_queue, handler) in self.handlers {
            let runner = QueueRunner::new(
                handler,
                Arc::clone(&self.state),
                Arc::clone(&metrics),
                cancel.clone(),
            );
            runner.start();
        }

        scheduler::run(pool, cancel).await;
    }
}
