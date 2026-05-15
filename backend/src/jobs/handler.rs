use std::sync::Arc;

use async_trait::async_trait;
use serde::de::DeserializeOwned;
use tokio_util::sync::CancellationToken;

use super::error::JobError;
use crate::state::AppState;

pub struct JobCtx {
    pub job_id: i64,
    pub attempt: i32,
    pub state: Arc<AppState>,
    pub cancel: CancellationToken,
}

impl JobCtx {
    pub fn is_cancelled(&self) -> bool {
        self.cancel.is_cancelled()
    }
}

/// Type-erased handler stored in the registry.
#[async_trait]
pub trait ErasedHandler: Send + Sync + 'static {
    fn queue(&self) -> &'static str;
    fn concurrency(&self) -> usize;
    fn max_attempts(&self) -> i32;
    async fn run_erased(&self, payload: serde_json::Value, ctx: JobCtx) -> Result<(), JobError>;
}

/// Typed handler — implement this for each job type.
#[async_trait]
pub trait JobHandler: Send + Sync + 'static {
    const QUEUE: &'static str;
    /// Maximum concurrent jobs of this type across the whole worker process.
    const CONCURRENCY: usize;
    const MAX_ATTEMPTS: i32 = 5;
    /// The payload struct deserialized from `jobs.payload`.
    type Args: DeserializeOwned + Send + Sync + 'static;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError>;
}

/// Blanket impl: any `JobHandler` automatically becomes an `ErasedHandler`.
#[async_trait]
impl<H: JobHandler> ErasedHandler for H {
    fn queue(&self) -> &'static str {
        H::QUEUE
    }
    fn concurrency(&self) -> usize {
        H::CONCURRENCY
    }
    fn max_attempts(&self) -> i32 {
        H::MAX_ATTEMPTS
    }

    async fn run_erased(&self, payload: serde_json::Value, ctx: JobCtx) -> Result<(), JobError> {
        let args: H::Args = serde_json::from_value(payload)?;
        self.run(args, ctx).await
    }
}
