use std::{
    sync::{Arc, RwLock},
    time::Duration,
};

use fred::clients::Client as RedisClient;
use moka::future::Cache;
use sqlx::PgPool;

use crate::config::AppConfig;
use crate::metrics::Metrics;

#[derive(Debug, Default, Clone)]
pub struct KeywordFilterCache {
    pub keywords: Vec<String>,  // active keywords, lowercased
    pub whitelist: Vec<String>, // whitelist phrases, lowercased
}

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
    /// In-memory cache of keyword filters and whitelist phrases.
    pub keyword_filters: Arc<RwLock<KeywordFilterCache>>,
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
            db_pool::build(ro_uri).await.unwrap_or_else(|e| {
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

        // Load keyword cache — sync_keywords_from_file is called after
        // migrate::run in main/worker so the schema is guaranteed to exist.
        let kf_cache = load_keyword_filter_cache(&pool).await;
        let keyword_filters = Arc::new(RwLock::new(kf_cache));

        Ok(Arc::new(Self {
            config,
            pool,
            pool_ro,
            redis,
            id_cache,
            http,
            metrics: Metrics::new(),
            telegram,
            keyword_filters,
        }))
    }
}

pub async fn load_keyword_filter_cache(pool: &PgPool) -> KeywordFilterCache {
    let keywords: Vec<String> = sqlx::query_scalar(
        "SELECT LOWER(keyword) FROM keyword_filters WHERE is_active = true ORDER BY keyword",
    )
    .fetch_all(pool)
    .await
    .unwrap_or_default();
    let whitelist: Vec<String> =
        sqlx::query_scalar("SELECT LOWER(phrase) FROM keyword_whitelist ORDER BY phrase")
            .fetch_all(pool)
            .await
            .unwrap_or_default();
    KeywordFilterCache {
        keywords,
        whitelist,
    }
}

/// Sync `keywords/adult-keywords.txt` into the DB.
///
/// Lines starting with `!` are whitelist entries; all other non-empty lines
/// are blocked keywords.  Only rows with `source = 'file'` are touched —
/// admin-managed entries are left untouched.
///
/// A SHA-256 of the raw file bytes is stored in `keyword_sync_state`.  If the
/// hash matches the stored value the sync is skipped entirely.
pub async fn sync_keywords_from_file(pool: &PgPool) {
    const FILE_CONTENT: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/resources/adult-keywords.txt"
    ));
    const SYNC_ID: &str = "adult-keywords";

    // ── Compute hash ──────────────────────────────────────────────────────────
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(FILE_CONTENT.as_bytes());
    let hash: String = digest.iter().map(|b| format!("{b:02x}")).collect();

    // ── Compare with stored hash ──────────────────────────────────────────────
    let stored: Option<String> =
        sqlx::query_scalar("SELECT file_hash FROM keyword_sync_state WHERE id = $1")
            .bind(SYNC_ID)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);

    if stored.as_deref() == Some(hash.as_str()) {
        tracing::debug!("keyword sync: file unchanged (hash {hash}), skipping");
        return;
    }

    tracing::info!("keyword sync: file changed, syncing to DB…");

    // ── Parse file ────────────────────────────────────────────────────────────
    let mut keywords: Vec<String> = Vec::new();
    let mut whitelist: Vec<String> = Vec::new();

    for line in FILE_CONTENT.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some(phrase) = trimmed.strip_prefix('!') {
            let phrase = phrase.trim().to_lowercase();
            if !phrase.is_empty() {
                whitelist.push(phrase);
            }
        } else {
            keywords.push(trimmed.to_lowercase());
        }
    }

    // ── Replace file-sourced rows atomically ──────────────────────────────────
    let mut tx = match pool.begin().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("keyword sync: failed to begin transaction: {e}");
            return;
        }
    };

    // Remove old file-sourced entries
    if let Err(e) = sqlx::query("DELETE FROM keyword_filters WHERE source = 'file'")
        .execute(&mut *tx)
        .await
    {
        tracing::error!("keyword sync: delete keyword_filters failed: {e}");
        return;
    }
    if let Err(e) = sqlx::query("DELETE FROM keyword_whitelist WHERE source = 'file'")
        .execute(&mut *tx)
        .await
    {
        tracing::error!("keyword sync: delete keyword_whitelist failed: {e}");
        return;
    }

    // Insert new keywords
    if !keywords.is_empty() {
        if let Err(e) = sqlx::query(
            "INSERT INTO keyword_filters (keyword, source)
             SELECT UNNEST($1::text[]), 'file'
             ON CONFLICT (LOWER(keyword)) DO UPDATE SET source = 'file', is_active = true",
        )
        .bind(&keywords[..])
        .execute(&mut *tx)
        .await
        {
            tracing::error!("keyword sync: insert keyword_filters failed: {e}");
            return;
        }
    }

    // Insert new whitelist phrases
    if !whitelist.is_empty() {
        if let Err(e) = sqlx::query(
            "INSERT INTO keyword_whitelist (phrase, reason, source)
             SELECT UNNEST($1::text[]), 'from keywords file', 'file'
             ON CONFLICT (LOWER(phrase)) DO UPDATE SET source = 'file'",
        )
        .bind(&whitelist[..])
        .execute(&mut *tx)
        .await
        {
            tracing::error!("keyword sync: insert keyword_whitelist failed: {e}");
            return;
        }
    }

    // Update stored hash
    if let Err(e) = sqlx::query(
        "INSERT INTO keyword_sync_state (id, file_hash, synced_at)
         VALUES ($1, $2, NOW())
         ON CONFLICT (id) DO UPDATE SET file_hash = EXCLUDED.file_hash, synced_at = NOW()",
    )
    .bind(SYNC_ID)
    .bind(&hash)
    .execute(&mut *tx)
    .await
    {
        tracing::error!("keyword sync: update keyword_sync_state failed: {e}");
        return;
    }

    if let Err(e) = tx.commit().await {
        tracing::error!("keyword sync: commit failed: {e}");
        return;
    }

    tracing::info!(
        "keyword sync: done — {} keywords, {} whitelist phrases (hash {hash})",
        keywords.len(),
        whitelist.len()
    );
}
