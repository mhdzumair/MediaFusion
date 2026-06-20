use std::{
    sync::{Arc, RwLock},
    time::Duration,
};

use fred::clients::Client as RedisClient;
use moka::future::Cache;
use sqlx::PgPool;

use crate::config::AppConfig;
use crate::db::MediaId;
use crate::metrics::Metrics;

#[derive(Debug, Default, Clone)]
pub struct KeywordFilterCache {
    pub keywords: Vec<String>,  // active keywords, lowercased
    pub whitelist: Vec<String>, // whitelist phrases, lowercased
}

impl KeywordFilterCache {
    /// Returns true when `text` matches an active blacklist keyword and is not whitelisted.
    pub fn matches_blocked_keyword(&self, text: &str) -> bool {
        if text.is_empty() {
            return false;
        }
        let lower = text.to_lowercase();
        if self
            .whitelist
            .iter()
            .any(|phrase| lower.contains(phrase.as_str()))
        {
            return false;
        }
        self.keywords
            .iter()
            .any(|keyword| lower.contains(keyword.as_str()))
    }

    /// Returns a SQL WHERE fragment that excludes media whose `m.title` is keyword-blocked.
    /// Relies on the precomputed `m.is_keyword_blocked` column (maintained by a DB trigger
    /// and `recompute_all_keyword_blocked()`).  Returns an empty string when the blocklist
    /// is empty so callers remain a no-op with zero overhead when no keywords are configured.
    pub fn keyword_title_block_fragment(&self) -> &'static str {
        if self.keywords.is_empty() {
            return "";
        }
        " AND m.is_keyword_blocked = false"
    }

    /// A deterministic tag derived from the current keyword/whitelist content.
    /// Embed this in Redis cache keys so that adding or removing keywords
    /// automatically invalidates previously-cached responses.
    pub fn version_tag(&self) -> u64 {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut h = DefaultHasher::new();
        self.keywords.hash(&mut h);
        self.whitelist.hash(&mut h);
        h.finish()
    }

    /// Remove genres whose names match blacklist keywords.
    pub fn filter_genres_by_type(
        &self,
        mut genres: std::collections::HashMap<String, Vec<String>>,
    ) -> std::collections::HashMap<String, Vec<String>> {
        for list in genres.values_mut() {
            list.retain(|name| !self.matches_blocked_keyword(name));
        }
        genres
    }
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
    pub id_cache: Cache<String, (MediaId, Vec<MediaId>)>,
    /// TVDB API-key → JWT cache. TVDB v4 requires exchanging the raw key for a
    /// short-lived JWT via POST /v4/login before each authenticated request.
    pub tvdb_jwt_cache: Cache<String, String>,
    /// HTTP client shared across all scrapers.
    pub http: reqwest::Client,
    /// Longer-timeout client for debrid playback resolution.
    pub debrid_http: reqwest::Client,
    /// No-proxy variants of the above; `Some` only when a proxy is configured and
    /// `REQUESTS_PROXY_EXCLUDE_DEBRID_PROVIDERS` is non-empty. Used by helper
    /// methods so excluded providers always connect directly.
    http_no_proxy: Option<reqwest::Client>,
    debrid_http_no_proxy: Option<reqwest::Client>,
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

        let base_pool_cfg = db_pool::PoolConfig {
            max_connections: config.db_pool_size,
            min_connections: config.db_pool_min,
            acquire_timeout_secs: config.db_acquire_timeout_secs,
            idle_timeout_secs: config.db_idle_timeout_secs,
            max_lifetime_secs: config.db_max_lifetime_secs,
            statement_timeout_ms: config.db_statement_timeout_ms,
            idle_in_transaction_timeout_ms: config.db_idle_tx_timeout_ms,
        };

        tracing::info!("connecting to PostgreSQL (primary)…");
        let pool = db_pool::build(&config.postgres_uri, base_pool_cfg.clone())
            .await
            .map_err(|e| format!("PostgreSQL primary: {e}"))?;

        let pool_ro = if let Some(ro_uri) = &config.postgres_ro_uri {
            tracing::info!("connecting to PostgreSQL (read-replica)…");
            let ro_cfg = db_pool::PoolConfig {
                max_connections: config.db_pool_size_ro.unwrap_or(config.db_pool_size),
                ..base_pool_cfg
            };
            db_pool::build(ro_uri, ro_cfg).await.unwrap_or_else(|e| {
                tracing::warn!("read-replica unavailable ({e}), falling back to primary");
                pool.clone()
            })
        } else {
            pool.clone()
        };

        tracing::info!("connecting to Redis…");
        let redis = redis_client::build(&config.redis_url).await?;

        let id_cache: Cache<String, (MediaId, Vec<MediaId>)> = Cache::builder()
            .max_capacity(50_000)
            .time_to_live(Duration::from_secs(300))
            .build();

        // TVDB JWTs are valid for ~30 days; cache for 23 h to refresh well before expiry.
        let tvdb_jwt_cache: Cache<String, String> = Cache::builder()
            .max_capacity(1_000)
            .time_to_live(Duration::from_secs(23 * 3600))
            .build();

        let http = crate::util::http::build(config.requests_proxy_url.as_deref(), config.tcp_keepalive_secs);
        let debrid_http = crate::util::http::build_debrid(config.requests_proxy_url.as_deref(), config.tcp_keepalive_secs);

        // Build no-proxy variants only when a proxy is configured AND there are
        // excluded providers — otherwise None (no allocation, no memory waste).
        let (http_no_proxy, debrid_http_no_proxy) = if config.requests_proxy_url.is_some()
            && !config.requests_proxy_exclude_debrid_providers.is_empty()
        {
            (
                Some(crate::util::http::build(None, config.tcp_keepalive_secs)),
                Some(crate::util::http::build_debrid(None, config.tcp_keepalive_secs)),
            )
        } else {
            (None, None)
        };

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
            tvdb_jwt_cache,
            http,
            debrid_http,
            http_no_proxy,
            debrid_http_no_proxy,
            metrics: Metrics::new(),
            telegram,
            keyword_filters,
        }))
    }

    /// Returns the appropriate general HTTP client for a debrid provider.
    /// Excluded providers (via `REQUESTS_PROXY_EXCLUDE_DEBRID_PROVIDERS`) use the
    /// no-proxy client so their traffic bypasses the gost/WARP tunnel directly.
    pub fn http_for_provider(&self, provider_id: &str) -> &reqwest::Client {
        if let Some(ref c) = self.http_no_proxy {
            if self.config.requests_proxy_exclude_debrid_providers
                .iter()
                .any(|id| id == provider_id)
            {
                return c;
            }
        }
        &self.http
    }

    /// Returns the appropriate long-timeout debrid HTTP client for a provider.
    pub fn debrid_http_for_provider(&self, provider_id: &str) -> &reqwest::Client {
        if let Some(ref c) = self.debrid_http_no_proxy {
            if self.config.requests_proxy_exclude_debrid_providers
                .iter()
                .any(|id| id == provider_id)
            {
                return c;
            }
        }
        &self.debrid_http
    }
}

const KW_BLOCKED_RECOMPUTE_ID: &str = "keyword-blocked-recompute";

/// Run `recompute_all_keyword_blocked()` without a statement timeout, then record
/// `version_tag` so [`maybe_recompute_keyword_blocked`] can skip future restarts
/// when keywords haven't changed.
///
/// Uses `SET LOCAL statement_timeout = 0` inside a transaction — the override is
/// scoped to that transaction and never leaks to other pool connections.
pub async fn recompute_keyword_blocked(pool: &PgPool, version_tag: u64) {
    let ver_str = format!("{:016x}", version_tag);

    // Get the current max ID so we know when to stop.
    let max_id: i32 = match sqlx::query_scalar::<_, Option<i32>>("SELECT MAX(id) FROM media")
        .fetch_one(pool)
        .await
    {
        Ok(Some(id)) => id,
        Ok(None) => {
            tracing::debug!("keyword blocked: media table is empty, skipping recompute");
            return;
        }
        Err(e) => {
            tracing::error!("recompute_all_keyword_blocked failed to get max id: {e}");
            return;
        }
    };

    // Batch the UPDATE to avoid holding a full-table lock for minutes, which deadlocks
    // concurrent scraper INSERTs/UPDATEs. Each small batch acquires and releases row locks
    // quickly, giving scrapers a chance to proceed between batches.
    const BATCH_SIZE: i32 = 500;
    let mut from_id: i32 = 0;

    loop {
        let to_id = from_id + BATCH_SIZE;
        let result = sqlx::query(
            "UPDATE media
             SET is_keyword_blocked = (
                 EXISTS (
                     SELECT 1 FROM keyword_filters kf
                     WHERE kf.is_active = true
                       AND position(LOWER(kf.keyword) IN LOWER(media.title)) > 0
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM keyword_whitelist kw
                     WHERE position(LOWER(kw.phrase) IN LOWER(media.title)) > 0
                 )
             )
             WHERE id > $1 AND id <= $2",
        )
        .bind(from_id)
        .bind(to_id)
        .execute(pool)
        .await;

        if let Err(e) = result {
            tracing::error!("recompute_all_keyword_blocked batch [{from_id}..{to_id}] failed: {e}");
            return;
        }

        from_id = to_id;
        if from_id >= max_id {
            break;
        }
    }

    // Record which keyword version was just recomputed so we skip on next startup.
    if let Err(e) = sqlx::query(
        "INSERT INTO keyword_sync_state (id, file_hash, synced_at) VALUES ($1, $2, NOW())
         ON CONFLICT (id) DO UPDATE SET file_hash = EXCLUDED.file_hash, synced_at = NOW()",
    )
    .bind(KW_BLOCKED_RECOMPUTE_ID)
    .bind(&ver_str)
    .execute(pool)
    .await
    {
        tracing::error!("recompute_all_keyword_blocked: failed to record version: {e}");
    } else {
        tracing::info!("keyword filter: is_keyword_blocked recomputed for all media");
    }
}

/// Check whether `is_keyword_blocked` needs a recompute and, if so, spawn one.
///
/// Compares the current keyword `version_tag` against the last-recomputed version
/// stored in `keyword_sync_state`.  On a normal restart with unchanged keywords
/// this is a single cheap SELECT and the heavy UPDATE is skipped entirely.
pub async fn maybe_recompute_keyword_blocked(pool: &PgPool, kf: &KeywordFilterCache) {
    if kf.keywords.is_empty() {
        return;
    }
    let ver = kf.version_tag();
    let ver_str = format!("{:016x}", ver);

    let stored: Option<String> =
        sqlx::query_scalar("SELECT file_hash FROM keyword_sync_state WHERE id = $1")
            .bind(KW_BLOCKED_RECOMPUTE_ID)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);

    if stored.as_deref() == Some(ver_str.as_str()) {
        tracing::debug!(
            "keyword blocked: column up to date (version {ver_str}), skipping recompute"
        );
        return;
    }

    tracing::info!("keyword blocked: version changed ({ver_str}), recomputing in background");
    let pool = pool.clone();
    tokio::spawn(async move { recompute_keyword_blocked(&pool, ver).await });
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

    let ver = KeywordFilterCache {
        keywords: keywords.clone(),
        whitelist: whitelist.clone(),
    }
    .version_tag();
    recompute_keyword_blocked(pool, ver).await;

    tracing::info!(
        "keyword sync: done — {} keywords, {} whitelist phrases (hash {hash})",
        keywords.len(),
        whitelist.len()
    );
}
