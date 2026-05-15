use std::collections::HashMap;
use std::sync::Arc;

use tokio_util::sync::CancellationToken;
use tracing::info;

use super::{handler::ErasedHandler, metrics::JobMetrics, runner::QueueRunner, scheduler};
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
