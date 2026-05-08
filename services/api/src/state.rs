use std::{sync::Arc, time::Duration};

use fred::clients::Client as RedisClient;
use moka::future::Cache;
use sqlx::PgPool;

use crate::config::AppConfig;
use crate::metrics::Metrics;

#[derive(Clone)]
pub struct AppState {
    pub config: AppConfig,
    /// Primary read-write pool.
    pub pool: PgPool,
    /// Read-only replica pool (falls back to `pool` if not configured).
    pub pool_ro: PgPool,
    pub redis: RedisClient,
    /// L1 in-process cache: "{imdb_id}:{media_type}" → (primary_id, related_ids)
    pub id_cache: Cache<String, (i64, Vec<i64>)>,
    /// HTTP client shared across all scrapers.
    pub http: reqwest::Client,
    /// HTTP request metrics collector.
    pub metrics: Arc<Metrics>,
    /// Optional Telegram MTProto client for live scraping (Phase 2c).
    /// None if TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_GRAMMERS_SESSION are not set.
    pub telegram: Option<Arc<grammers_client::Client>>,
}

impl AppState {
    pub async fn build(
        config: AppConfig,
    ) -> Result<Arc<Self>, Box<dyn std::error::Error + Send + Sync>> {
        use crate::{cache::client as redis_client, db::pool as db_pool};

        tracing::info!("connecting to PostgreSQL (primary)…");
        let pool = db_pool::build(&config.postgres_uri)
            .await
            .map_err(|e| format!("PostgreSQL primary: {e}"))?;

        let pool_ro = if let Some(ro_uri) = &config.postgres_ro_uri {
            tracing::info!("connecting to PostgreSQL (read-replica)…");
            db_pool::build(ro_uri)
                .await
                .unwrap_or_else(|e| {
                    tracing::warn!("read-replica unavailable ({e}), falling back to primary");
                    pool.clone()
                })
        } else {
            pool.clone()
        };

        tracing::info!("connecting to Redis…");
        let redis = redis_client::build(&config.redis_url).await?;

        let id_cache: Cache<String, (i64, Vec<i64>)> = Cache::builder()
            .max_capacity(50_000)
            .time_to_live(Duration::from_secs(300))
            .build();

        let http = crate::util::http::build();

        let telegram = crate::scrapers::telegram::init_client(&config).await;

        Ok(Arc::new(Self {
            config,
            pool,
            pool_ro,
            redis,
            id_cache,
            http,
            metrics: Metrics::new(),
            telegram,
        }))
    }
}
