use std::sync::Arc;
use std::time::Duration;

use prometheus_client::encoding::EncodeLabelSet;
use prometheus_client::metrics::counter::Counter;
use prometheus_client::metrics::family::Family;
use prometheus_client::metrics::gauge::Gauge;
use prometheus_client::metrics::histogram::{exponential_buckets, Histogram};
use prometheus_client::registry::Registry;
use sqlx::PgPool;
use tokio::time;
use tokio_util::sync::CancellationToken;

#[derive(Clone, Debug, Hash, PartialEq, Eq, EncodeLabelSet)]
pub struct QueueLabels {
    pub queue: String,
}

#[derive(Clone, Debug, Hash, PartialEq, Eq, EncodeLabelSet)]
pub struct OutcomeLabels {
    pub queue: String,
    pub outcome: String,
}

pub struct JobMetrics {
    pub queue_depth: Family<QueueLabels, Gauge>,
    pub duration_seconds: Family<OutcomeLabels, Histogram>,
    pub attempts_total: Family<OutcomeLabels, Counter>,
}

impl JobMetrics {
    pub fn new(registry: &mut Registry) -> Self {
        let queue_depth = Family::<QueueLabels, Gauge>::default();
        let duration_seconds = Family::<OutcomeLabels, Histogram>::new_with_constructor(|| {
            Histogram::new(exponential_buckets(0.1, 2.0, 14))
        });
        let attempts_total = Family::<OutcomeLabels, Counter>::default();

        registry.register(
            "mf_job_queue_depth",
            "Number of pending jobs per queue",
            queue_depth.clone(),
        );
        registry.register(
            "mf_job_duration_seconds",
            "Job execution duration in seconds",
            duration_seconds.clone(),
        );
        registry.register(
            "mf_job_attempts_total",
            "Total job attempts by queue and outcome",
            attempts_total.clone(),
        );

        Self {
            queue_depth,
            duration_seconds,
            attempts_total,
        }
    }

    pub fn record_outcome(&self, queue: &str, outcome: &str, elapsed: Duration) {
        let labels = OutcomeLabels {
            queue: queue.to_string(),
            outcome: outcome.to_string(),
        };
        self.duration_seconds
            .get_or_create(&labels)
            .observe(elapsed.as_secs_f64());
        self.attempts_total.get_or_create(&labels).inc();
    }

    /// Background task: poll DB every 10s to refresh queue depth gauges.
    pub fn start_depth_poller(
        metrics: Arc<JobMetrics>,
        pool: Arc<PgPool>,
        cancel: CancellationToken,
    ) {
        tokio::spawn(async move {
            let mut interval = time::interval(Duration::from_secs(10));
            loop {
                tokio::select! {
                    _ = cancel.cancelled() => break,
                    _ = interval.tick() => {
                        if let Ok(rows) = sqlx::query!(
                            "SELECT queue, COUNT(*) as cnt FROM jobs \
                             WHERE status = 'pending' GROUP BY queue"
                        )
                        .fetch_all(pool.as_ref())
                        .await
                        {
                            for row in rows {
                                let labels = QueueLabels { queue: row.queue };
                                metrics.queue_depth.get_or_create(&labels)
                                    .set(row.cnt.unwrap_or(0));
                            }
                        }
                    }
                }
            }
        });
    }
}
