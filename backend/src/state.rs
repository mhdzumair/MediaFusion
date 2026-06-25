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
use crate::nsfw::NsfwClassifier;

#[derive(Debug, Default, Clone)]
pub struct KeywordFilterCache {
    /// Media-scoped keywords (scope 'media' or 'all'): drive is_keyword_blocked DB column
    /// and in-memory media title checks (e.g. MDBList API path).
    pub keywords: Vec<String>,
    /// Stream-scoped keywords (scope 'stream' or 'all'): for torrent/stream title filtering.
    pub stream_keywords: Vec<String>,
    pub whitelist: Vec<String>, // whitelist phrases, lowercased
    /// Mirrors `AppConfig::poster_nsfw_enabled`; set after loading from DB.
    pub nsfw_filter_enabled: bool,
}

impl KeywordFilterCache {
    /// Returns true when `text` (a stream/torrent title) matches an active stream-scoped
    /// keyword (scope 'stream' or 'all') and is not whitelisted.
    /// Used at scrape/import time to skip blocked torrent titles before they enter the DB.
    pub fn matches_blocked_keyword(&self, text: &str) -> bool {
        Self::matches_list(&self.stream_keywords, &self.whitelist, text)
    }

    /// Returns true when `text` (a media title or description) matches an active media-scoped
    /// keyword (scope 'media' or 'all') and is not whitelisted.
    /// Used for in-memory media title checks (e.g. MDBList API path, meta, discover).
    pub fn matches_blocked_media_keyword(&self, text: &str) -> bool {
        Self::matches_list(&self.keywords, &self.whitelist, text)
    }

    /// True if `keyword` appears in `text` as a whole word (not inside a longer word).
    /// Both `text` and `keyword` must already be lowercased.
    fn whole_word_match(text: &str, keyword: &str) -> bool {
        if keyword.is_empty() {
            return false;
        }
        let klen = keyword.len();
        let mut search = text;
        let mut offset = 0usize;
        while let Some(pos) = search.find(keyword) {
            let abs = offset + pos;
            let before_ok = abs == 0 || {
                let ch = text[..abs].chars().next_back().unwrap();
                !ch.is_alphanumeric() && ch != '_'
            };
            let end = abs + klen;
            let after_ok = end >= text.len() || {
                let ch = text[end..].chars().next().unwrap();
                !ch.is_alphanumeric() && ch != '_'
            };
            if before_ok && after_ok {
                return true;
            }
            // Advance past this occurrence to find the next one.
            offset += pos + 1;
            search = &text[offset..];
            if search.len() < klen {
                break;
            }
        }
        false
    }

    fn matches_list(keywords: &[String], whitelist: &[String], text: &str) -> bool {
        if text.is_empty() || keywords.is_empty() {
            return false;
        }
        let lower = text.to_lowercase();
        // Whitelist uses substring match (permissive — any overlap clears the block).
        if whitelist
            .iter()
            .any(|phrase| lower.contains(phrase.as_str()))
        {
            return false;
        }
        // Keywords use whole-word match: "cock" must not match inside "cocktail".
        keywords.iter().any(|kw| Self::whole_word_match(&lower, kw))
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

    /// SQL WHERE fragment that excludes NSFW-flagged posters.
    /// Returns an empty string when `nsfw_filter_enabled` is false so callers
    /// are a no-op with zero overhead when the feature is disabled.
    pub fn nsfw_block_fragment(&self) -> &'static str {
        if self.nsfw_filter_enabled {
            " AND m.poster_nsfw_flagged = false"
        } else {
            ""
        }
    }

    /// A deterministic tag derived from the current keyword/whitelist content.
    /// Embed this in Redis cache keys so that adding or removing keywords
    /// automatically invalidates previously-cached responses.
    pub fn version_tag(&self) -> u64 {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut h = DefaultHasher::new();
        self.keywords.hash(&mut h);
        self.stream_keywords.hash(&mut h);
        self.whitelist.hash(&mut h);
        h.finish()
    }

    /// A deterministic tag derived only from media-scoped keywords and whitelist.
    /// Used for the `is_keyword_blocked` recompute check — only media keywords
    /// drive the DB column, so stream-only keyword changes should not trigger a recompute.
    pub fn media_version_tag(&self) -> u64 {
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
            list.retain(|name| !self.matches_blocked_media_keyword(name));
        }
        genres
    }
}

// ─── Unified restriction helpers ─────────────────────────────────────────────

/// SQL WHERE fragment (alias `m`) that excludes **all** restricted media:
/// manually blocked, keyword-blocked, or NSFW-flagged.
///
/// Always active — does not depend on any runtime config.  Use this on every
/// catalog / search / meta query that should hide restricted content from
/// regular users.  The three exempt admin endpoints check `media_is_restricted`
/// separately and bypass this fragment for admin-role callers.
pub fn restriction_fragment() -> &'static str {
    " AND NOT (m.is_blocked OR (m.is_keyword_blocked AND NOT m.keyword_block_override) OR m.poster_nsfw_flagged)"
}

/// Returns `true` when the given media row is restricted (manually blocked,
/// keyword-blocked, or NSFW-flagged with no "safe" review).
///
/// Used by single-row guard points (`get_media_detail`, `get_media_streams`,
/// `get_media_metadata`, `stream.rs` build_pipeline).
pub async fn media_is_restricted(pool: &PgPool, media_id: i32) -> bool {
    sqlx::query_scalar::<_, bool>(
        "SELECT (is_blocked OR (is_keyword_blocked AND NOT keyword_block_override) OR poster_nsfw_flagged) FROM media WHERE id = $1",
    )
    .bind(media_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)
    .unwrap_or(false)
}

/// Returns `true` when **all** media linked to the given torrent `info_hash` are
/// restricted.  A torrent shared with at least one unrestricted title is allowed
/// through.  Returns `false` when no media is linked (non-media stream).
///
/// Used by `playback.rs` `resolve()` as a final defence-in-depth check.
pub async fn info_hash_is_restricted(pool: &PgPool, info_hash: &str) -> bool {
    // CTE yields one bool-row per linked media record.  COUNT(*) > 0 ensures
    // we don't block info_hashes with no linked media at all.
    sqlx::query_scalar::<_, bool>(
        r#"
        WITH linked AS (
            SELECT (m.is_blocked OR (m.is_keyword_blocked AND NOT m.keyword_block_override) OR m.poster_nsfw_flagged) AS restricted
            FROM torrent_stream ts
            JOIN stream s ON s.id = ts.stream_id
            JOIN stream_media_link sml ON sml.stream_id = s.id
            JOIN media m ON m.id = sml.media_id
            WHERE ts.info_hash = $1
        )
        SELECT COUNT(*) > 0 AND bool_and(restricted) FROM linked
        "#,
    )
    .bind(info_hash)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)
    .unwrap_or(false)
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
    /// ONNX-based NSFW poster classifier. `None` when the model file is absent
    /// or `POSTER_NSFW_ENABLED=false`.
    pub nsfw_classifier: Option<Arc<NsfwClassifier>>,
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

        let general_proxy = if config.requests_proxy_non_debrid_enabled {
            config.requests_proxy_url.as_deref()
        } else {
            None
        };
        let http = crate::util::http::build(general_proxy, config.tcp_keepalive_secs);
        let debrid_http = crate::util::http::build_debrid(
            config.requests_proxy_url.as_deref(),
            config.tcp_keepalive_secs,
        );

        // Build no-proxy variants when a proxy is configured AND either an include
        // or exclude list is set (both modes need a direct-egress client).
        let needs_no_proxy = config.requests_proxy_url.is_some()
            && (!config.requests_proxy_exclude_debrid_providers.is_empty()
                || !config.requests_proxy_include_debrid_providers.is_empty());
        let (http_no_proxy, debrid_http_no_proxy) = if needs_no_proxy {
            (
                Some(crate::util::http::build(None, config.tcp_keepalive_secs)),
                Some(crate::util::http::build_debrid(
                    None,
                    config.tcp_keepalive_secs,
                )),
            )
        } else {
            (None, None)
        };

        let telegram = crate::scrapers::telegram::init_client(&config).await;

        // Load keyword cache — sync_keywords_from_file is called after
        // migrate::run in main/worker so the schema is guaranteed to exist.
        let mut kf_cache = load_keyword_filter_cache(&pool).await;
        kf_cache.nsfw_filter_enabled = config.poster_nsfw_enabled;
        let keyword_filters = Arc::new(RwLock::new(kf_cache));

        // Load NSFW classifier when enabled and model file is present.
        let nsfw_classifier = if config.poster_nsfw_enabled {
            NsfwClassifier::load(&config.poster_nsfw_model_path).map(Arc::new)
        } else {
            None
        };

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
            nsfw_classifier,
        }))
    }

    /// Returns the appropriate general HTTP client for a debrid provider.
    ///
    /// Include mode (`REQUESTS_PROXY_INCLUDE_DEBRID_PROVIDERS` non-empty): only the
    /// listed providers use the proxy; all others connect directly.
    /// Exclude mode (`REQUESTS_PROXY_EXCLUDE_DEBRID_PROVIDERS` non-empty): all providers
    /// use the proxy except the listed ones.
    pub fn http_for_provider(&self, provider_id: &str) -> &reqwest::Client {
        if let Some(ref no_proxy) = self.http_no_proxy {
            if !self
                .config
                .requests_proxy_include_debrid_providers
                .is_empty()
            {
                // Include mode: return no-proxy unless provider is in the include list.
                if !self
                    .config
                    .requests_proxy_include_debrid_providers
                    .iter()
                    .any(|id| id == provider_id)
                {
                    return no_proxy;
                }
            } else if self
                .config
                .requests_proxy_exclude_debrid_providers
                .iter()
                .any(|id| id == provider_id)
            {
                return no_proxy;
            }
        }
        &self.http
    }

    /// Returns the appropriate long-timeout debrid HTTP client for a provider.
    pub fn debrid_http_for_provider(&self, provider_id: &str) -> &reqwest::Client {
        if let Some(ref no_proxy) = self.debrid_http_no_proxy {
            if !self
                .config
                .requests_proxy_include_debrid_providers
                .is_empty()
            {
                if !self
                    .config
                    .requests_proxy_include_debrid_providers
                    .iter()
                    .any(|id| id == provider_id)
                {
                    return no_proxy;
                }
            } else if self
                .config
                .requests_proxy_exclude_debrid_providers
                .iter()
                .any(|id| id == provider_id)
            {
                return no_proxy;
            }
        }
        &self.debrid_http
    }

    /// Returns the no-proxy HTTP client (if configured), the include list, and the
    /// exclude list. Used by validation paths that need per-provider client selection.
    ///
    /// When `include` is non-empty it takes precedence over `exclude`.
    pub fn proxy_bypass_clients(&self) -> (Option<&reqwest::Client>, &[String], &[String]) {
        (
            self.http_no_proxy.as_ref(),
            &self.config.requests_proxy_include_debrid_providers,
            &self.config.requests_proxy_exclude_debrid_providers,
        )
    }
}

const KW_BLOCKED_RECOMPUTE_ID: &str = "keyword-blocked-recompute";

/// Run `recompute_all_keyword_blocked()` without a statement timeout, then record
/// `version_tag` so [`maybe_recompute_keyword_blocked`] can skip future restarts
/// when keywords haven't changed.
///
/// Uses `SET LOCAL statement_timeout = 0` inside a transaction — the override is
/// scoped to that transaction and never leaks to other pool connections.
/// Joins `terms` into a single POSIX regex alternation `(t1|t2|...)` with
/// metacharacters escaped.  Returns `None` when the list is empty so callers
/// can pass `NULL` to PostgreSQL and skip the pattern match entirely.
fn build_regex_pattern(terms: &[String]) -> Option<String> {
    if terms.is_empty() {
        return None;
    }
    let escaped = terms
        .iter()
        .map(|t| {
            let mut out = String::with_capacity(t.len() + 4);
            for c in t.chars() {
                if matches!(
                    c,
                    '\\' | '.'
                        | '^'
                        | '$'
                        | '*'
                        | '+'
                        | '?'
                        | '('
                        | ')'
                        | '['
                        | ']'
                        | '{'
                        | '}'
                        | '|'
                ) {
                    out.push('\\');
                }
                out.push(c);
            }
            out
        })
        .collect::<Vec<_>>()
        .join("|");
    // Wrap with word-boundary assertions so "cock" doesn't match inside "cocktail".
    Some(format!("\\y({escaped})\\y"))
}

pub async fn recompute_keyword_blocked(
    pool: &PgPool,
    version_tag: u64,
    keywords: &[String],
    whitelist: &[String],
) {
    let ver_str = format!("{:016x}", version_tag);

    // Compile the full keyword/whitelist lists into single regex alternations once.
    // PostgreSQL's NFA evaluates one pattern per row in O(len(text)) regardless of
    // how many alternations are present — vastly cheaper than 1 position() call per
    // keyword per row (1069 keywords × 500 rows = 534 500 substring scans/batch).
    let kw_pattern: Option<String> = build_regex_pattern(keywords);
    let wl_pattern: Option<String> = build_regex_pattern(whitelist);

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
    //
    // Perf notes:
    // - kw_pattern / wl_pattern are pre-compiled regex alternations passed as $3/$4.
    //   One NFA evaluation per field per row instead of N_keywords position() calls.
    // - LOWER() computed once per row in the `batch` CTE.
    // - IS DISTINCT FROM skips unchanged rows, reducing write and WAL overhead.
    // - NULL pattern ($3/$4) short-circuits the match entirely via IS NOT NULL guard.
    const BATCH_SIZE: i32 = 500;
    let mut from_id: i32 = 0;
    let mut total_updated: u64 = 0;
    let started = std::time::Instant::now();

    loop {
        let to_id = from_id + BATCH_SIZE;
        let result = sqlx::query(
            "WITH batch AS (
                 SELECT id,
                        LOWER(title)                     AS ltitle,
                        LOWER(COALESCE(description, '')) AS ldesc,
                        adult
                 FROM media
                 WHERE id > $1 AND id <= $2
             ),
             computed AS (
                 SELECT b.id,
                        b.adult
                        OR (
                            $3::text IS NOT NULL
                            AND (b.ltitle ~ $3 OR b.ldesc ~ $3)
                            AND ($4::text IS NULL OR NOT (b.ltitle ~ $4 OR b.ldesc ~ $4))
                        ) AS new_blocked
                 FROM batch b
             )
             UPDATE media m
             SET is_keyword_blocked = c.new_blocked
             FROM computed c
             WHERE m.id = c.id
               AND m.is_keyword_blocked IS DISTINCT FROM c.new_blocked",
        )
        .bind(from_id)
        .bind(to_id)
        .bind(&kw_pattern)
        .bind(&wl_pattern)
        .execute(pool)
        .await;

        match result {
            Ok(r) => total_updated += r.rows_affected(),
            Err(e) => {
                tracing::error!(
                    "recompute_all_keyword_blocked batch [{from_id}..{to_id}] failed: {e}"
                );
                return;
            }
        }

        from_id = to_id;
        if from_id >= max_id {
            break;
        }
    }

    // Record which keyword version was just recomputed so we skip on next startup.
    match sqlx::query(
        "INSERT INTO keyword_sync_state (id, file_hash, synced_at) VALUES ($1, $2, NOW())
         ON CONFLICT (id) DO UPDATE SET file_hash = EXCLUDED.file_hash, synced_at = NOW()",
    )
    .bind(KW_BLOCKED_RECOMPUTE_ID)
    .bind(&ver_str)
    .execute(pool)
    .await
    {
        Err(e) => {
            tracing::error!("recompute_all_keyword_blocked: failed to record version: {e}");
        }
        _ => {
            let elapsed = started.elapsed();
            tracing::info!(
                keywords = keywords.len(),
                updated = total_updated,
                elapsed_ms = elapsed.as_millis(),
                "keyword filter: media is_keyword_blocked recompute complete"
            );
        }
    }
}

const STREAM_KW_BLOCKED_RECOMPUTE_ID: &str = "stream-keyword-blocked-recompute";

/// Batch-recompute `stream.is_keyword_blocked` for all rows.
///
/// Mirrors `recompute_keyword_blocked` but targets the `stream` table using
/// stream-scoped keywords (scope 'stream' or 'all').
pub async fn recompute_stream_keyword_blocked(
    pool: &PgPool,
    version_tag: u64,
    stream_keywords: &[String],
    whitelist: &[String],
) {
    let ver_str = format!("{:016x}", version_tag);

    let kw_pattern: Option<String> = build_regex_pattern(stream_keywords);
    let wl_pattern: Option<String> = build_regex_pattern(whitelist);

    // Get the current max ID so we know when to stop.
    let max_id: i32 = match sqlx::query_scalar::<_, Option<i32>>("SELECT MAX(id) FROM stream")
        .fetch_one(pool)
        .await
    {
        Ok(Some(id)) => id,
        Ok(None) => {
            tracing::debug!("stream keyword blocked: stream table is empty, skipping recompute");
            return;
        }
        Err(e) => {
            tracing::error!("recompute_stream_keyword_blocked failed to get max id: {e}");
            return;
        }
    };

    const BATCH_SIZE: i32 = 500;
    let mut from_id: i32 = 0;
    let mut total_updated: u64 = 0;
    let started = std::time::Instant::now();

    loop {
        let to_id = from_id + BATCH_SIZE;
        let result = sqlx::query(
            "UPDATE stream s
             SET is_keyword_blocked = (
                 $3::text IS NOT NULL
                 AND s.name ~* $3
                 AND ($4::text IS NULL OR s.name !~* $4)
             )
             WHERE s.id > $1 AND s.id <= $2
               AND s.is_keyword_blocked IS DISTINCT FROM (
                   $3::text IS NOT NULL
                   AND s.name ~* $3
                   AND ($4::text IS NULL OR s.name !~* $4)
               )",
        )
        .bind(from_id)
        .bind(to_id)
        .bind(&kw_pattern)
        .bind(&wl_pattern)
        .execute(pool)
        .await;

        match result {
            Ok(r) => total_updated += r.rows_affected(),
            Err(e) => {
                // NotificationResponse errors are transient pool-connection issues (a connection
                // that previously did LISTEN received an async notification during query execution).
                // Skip the batch and continue rather than aborting the entire recompute.
                let msg = e.to_string();
                if msg.contains("NotificationResponse") {
                    tracing::warn!(
                        "recompute_stream_keyword_blocked batch [{from_id}..{to_id}] skipped \
                         due to transient NotificationResponse on pool connection: {e}"
                    );
                } else {
                    tracing::error!(
                        "recompute_stream_keyword_blocked batch [{from_id}..{to_id}] failed: {e}"
                    );
                    return;
                }
            }
        }

        from_id = to_id;
        if from_id >= max_id {
            break;
        }
    }

    // Record which keyword version was just recomputed so we skip on next startup.
    match sqlx::query(
        "INSERT INTO keyword_sync_state (id, file_hash, synced_at) VALUES ($1, $2, NOW())
         ON CONFLICT (id) DO UPDATE SET file_hash = EXCLUDED.file_hash, synced_at = NOW()",
    )
    .bind(STREAM_KW_BLOCKED_RECOMPUTE_ID)
    .bind(&ver_str)
    .execute(pool)
    .await
    {
        Err(e) => {
            tracing::error!("recompute_stream_keyword_blocked: failed to record version: {e}");
        }
        _ => {
            let elapsed = started.elapsed();
            tracing::info!(
                keywords = stream_keywords.len(),
                updated = total_updated,
                elapsed_ms = elapsed.as_millis(),
                "keyword filter: stream is_keyword_blocked recompute complete"
            );
        }
    }
}

/// Check whether stream `is_keyword_blocked` needs a recompute and, if so, spawn one.
pub async fn maybe_recompute_stream_keyword_blocked(pool: &PgPool, kf: &KeywordFilterCache) {
    // Use the full version_tag so any keyword/whitelist change (including media-scope changes
    // that may have crossover with stream scope) triggers a stream recompute too.
    let ver = kf.version_tag();
    let ver_str = format!("{:016x}", ver);

    let stored: Option<String> =
        sqlx::query_scalar("SELECT file_hash FROM keyword_sync_state WHERE id = $1")
            .bind(STREAM_KW_BLOCKED_RECOMPUTE_ID)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);

    if stored.as_deref() == Some(ver_str.as_str()) {
        tracing::debug!(
            "stream keyword blocked: column up to date (version {ver_str}), skipping recompute"
        );
        return;
    }

    tracing::info!(
        "stream keyword blocked: version changed ({ver_str}), recomputing in background"
    );
    let pool = pool.clone();
    let stream_keywords = kf.stream_keywords.clone();
    let whitelist = kf.whitelist.clone();
    tokio::spawn(async move {
        recompute_stream_keyword_blocked(&pool, ver, &stream_keywords, &whitelist).await
    });
}

/// Check whether `is_keyword_blocked` needs a recompute and, if so, spawn one.
///
/// Compares the current keyword `version_tag` against the last-recomputed version
/// stored in `keyword_sync_state`.  On a normal restart with unchanged keywords
/// this is a single cheap SELECT and the heavy UPDATE is skipped entirely.
pub async fn maybe_recompute_keyword_blocked(pool: &PgPool, kf: &KeywordFilterCache) {
    let ver = kf.media_version_tag();
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
    let keywords = kf.keywords.clone();
    let whitelist = kf.whitelist.clone();
    tokio::spawn(async move { recompute_keyword_blocked(&pool, ver, &keywords, &whitelist).await });
}

pub async fn load_keyword_filter_cache(pool: &PgPool) -> KeywordFilterCache {
    let keywords: Vec<String> = sqlx::query_scalar(
        "SELECT LOWER(keyword) FROM keyword_filters WHERE is_active = true AND scope IN ('all', 'media') ORDER BY keyword",
    )
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    let stream_keywords: Vec<String> = sqlx::query_scalar(
        "SELECT LOWER(keyword) FROM keyword_filters WHERE is_active = true AND scope IN ('all', 'stream') ORDER BY keyword",
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
        stream_keywords,
        whitelist,
        nsfw_filter_enabled: false, // caller sets this from config after loading
    }
}

/// Sync `keywords/media-keywords.txt` into the DB.
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
        "/resources/media-keywords-filters.txt"
    ));
    const SYNC_ID: &str = "media-keywords";

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

    // Remove old file-sourced media-scope entries (leaves stream-scope file rows intact)
    if let Err(e) =
        sqlx::query("DELETE FROM keyword_filters WHERE source = 'file' AND scope = 'media'")
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
    if !keywords.is_empty()
        && let Err(e) = sqlx::query(
            "INSERT INTO keyword_filters (keyword, source, scope)
             SELECT UNNEST($1::text[]), 'file', 'media'
             ON CONFLICT (LOWER(keyword)) DO UPDATE SET source = 'file', is_active = true, scope = 'media'",
        )
        .bind(&keywords[..])
        .execute(&mut *tx)
        .await
        {
            tracing::error!("keyword sync: insert keyword_filters failed: {e}");
            return;
        }

    // Insert new whitelist phrases
    if !whitelist.is_empty()
        && let Err(e) = sqlx::query(
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
        stream_keywords: vec![],
        whitelist: whitelist.clone(),
        nsfw_filter_enabled: false,
    }
    .media_version_tag();
    recompute_keyword_blocked(pool, ver, &keywords, &whitelist).await;

    tracing::info!(
        "keyword sync: done — {} keywords, {} whitelist phrases (hash {hash})",
        keywords.len(),
        whitelist.len()
    );

    // Sync stream-scoped keywords from the companion file.
    sync_stream_keywords_from_file(pool).await;
}

/// Sync `keywords/stream-keywords.txt` into the DB with `scope='stream'`.
///
/// Lines starting with `#` are comment lines and are skipped.  Only rows with
/// `source = 'file' AND scope = 'stream'` are touched — admin-managed entries
/// are left untouched.
///
/// A SHA-256 of the raw file bytes is stored in `keyword_sync_state`.  If the
/// hash matches the stored value the sync is skipped entirely.
pub async fn sync_stream_keywords_from_file(pool: &PgPool) {
    const FILE_CONTENT: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/resources/stream-keywords-filters.txt"
    ));
    const SYNC_ID: &str = "stream-keywords";

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
        tracing::debug!("stream keyword sync: file unchanged (hash {hash}), skipping");
        return;
    }

    tracing::info!("stream keyword sync: file changed, syncing to DB…");

    // ── Parse file ────────────────────────────────────────────────────────────
    let mut stream_keywords: Vec<String> = Vec::new();

    for line in FILE_CONTENT.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        stream_keywords.push(trimmed.to_lowercase());
    }

    // ── Replace file-sourced stream-scope rows atomically ─────────────────────
    let mut tx = match pool.begin().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("stream keyword sync: failed to begin transaction: {e}");
            return;
        }
    };

    // Remove old file-sourced stream-scope entries
    if let Err(e) =
        sqlx::query("DELETE FROM keyword_filters WHERE source = 'file' AND scope = 'stream'")
            .execute(&mut *tx)
            .await
    {
        tracing::error!("stream keyword sync: delete keyword_filters failed: {e}");
        return;
    }

    // Insert new stream keywords (skip if empty)
    if !stream_keywords.is_empty()
        && let Err(e) = sqlx::query(
            "INSERT INTO keyword_filters (keyword, source, scope)
             SELECT UNNEST($1::text[]), 'file', 'stream'
             ON CONFLICT (LOWER(keyword)) DO UPDATE SET source = 'file', is_active = true, scope = 'stream'",
        )
        .bind(&stream_keywords[..])
        .execute(&mut *tx)
        .await
        {
            tracing::error!("stream keyword sync: insert keyword_filters failed: {e}");
            return;
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
        tracing::error!("stream keyword sync: update keyword_sync_state failed: {e}");
        return;
    }

    if let Err(e) = tx.commit().await {
        tracing::error!("stream keyword sync: commit failed: {e}");
        return;
    }

    // Load the current whitelist for the recompute
    let whitelist: Vec<String> =
        sqlx::query_scalar("SELECT LOWER(phrase) FROM keyword_whitelist ORDER BY phrase")
            .fetch_all(pool)
            .await
            .unwrap_or_default();

    let ver = KeywordFilterCache {
        keywords: vec![],
        stream_keywords: stream_keywords.clone(),
        whitelist: whitelist.clone(),
        nsfw_filter_enabled: false,
    }
    .version_tag();
    recompute_stream_keyword_blocked(pool, ver, &stream_keywords, &whitelist).await;

    tracing::info!(
        "stream keyword sync: done — {} keywords (hash {hash})",
        stream_keywords.len()
    );
}
