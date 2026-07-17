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

    fn matches_list(keywords: &[String], whitelist: &[String], text: &str) -> bool {
        if text.is_empty() || keywords.is_empty() {
            return false;
        }
        let lower = text.to_lowercase();
        if whitelist
            .iter()
            .any(|phrase| lower.contains(phrase.as_str()))
        {
            return false;
        }
        keywords.iter().any(|kw| lower.contains(kw.as_str()))
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
/// metacharacters escaped.  Substring match — no word boundaries, so plurals and
/// conjugations are captured.  Returns `None` when the list is empty so callers
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
    Some(format!("({escaped})"))
}

async fn recompute_keyword_blocked(
    pool: &PgPool,
    keywords: &[String],
    whitelist: &[String],
) -> bool {
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
            // An empty table is trivially converged.
            tracing::debug!("keyword blocked: media table is empty, nothing to sweep");
            return true;
        }
        Err(e) => {
            tracing::error!("recompute_all_keyword_blocked failed to get max id: {e}");
            return false;
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
                return false;
            }
        }

        from_id = to_id;
        if from_id >= max_id {
            break;
        }
    }

    // Completion-marker publication lives in kw_recompute_single_flight, fenced
    // on lease ownership — a sweep that lost its lease must not publish.
    let elapsed = started.elapsed();
    tracing::info!(
        keywords = keywords.len(),
        updated = total_updated,
        elapsed_ms = elapsed.as_millis(),
        "keyword filter: media is_keyword_blocked recompute complete"
    );
    true
}

const STREAM_KW_BLOCKED_RECOMPUTE_ID: &str = "stream-keyword-blocked-recompute";

// Deployment-wide single-flight leases for the keyword-blocked recomputes.
// `keyword_filters` is a global table, so every process computes the identical
// result — and even a minimal deployment runs the API and the worker as
// separate processes, both of which trigger recomputes at startup. Without
// coordination, each process that starts (or restarts mid-sweep, since
// completion is only recorded at the end) launches its own full-table regex
// recompute; with several replicas these overlapping sweeps contend on the
// same tuples and can dominate database CPU.
//
// Coordination uses a lease row in the existing `keyword_sync_state` table
// rather than `pg_advisory_lock`: deployments may route the app through
// PgBouncer in transaction pooling mode, where session advisory locks
// land on an arbitrary backend that is immediately returned to the pool — the
// lock can leak, unlock on a different backend, and fail to exclude anyone.
// The lease claim is a single-statement atomic upsert (transaction-pooling
// safe) and holds no pool connection while the sweep runs, so it also cannot
// deadlock a DB_POOL_SIZE=1 pool. The winner renews the lease periodically;
// if it dies, the lease goes stale and the next starting process claims it.
const RECOMPUTE_LEASE_STALE_SECS: f64 = 180.0;
const RECOMPUTE_LEASE_RENEW_SECS: u64 = 60;
// Must stay comfortably below RECOMPUTE_LEASE_STALE_SECS: a holder that cannot
// confirm a renewal for this long aborts its sweep BEFORE the lease can go
// stale under it and a successor can claim.
const RECOMPUTE_RENEW_ABORT_SECS: u64 = 120;
const RECOMPUTE_RETRY_SLEEP_SECS: u64 = 180;
const RECOMPUTE_MAX_ATTEMPTS: u32 = 48;
const MEDIA_KW_RECOMPUTE_LEASE_ID: &str = "keyword-blocked-recompute-lease";
const STREAM_KW_RECOMPUTE_LEASE_ID: &str = "stream-keyword-blocked-recompute-lease";

/// Read the recorded completion version for `marker_id` (e.g.
/// [`STREAM_KW_BLOCKED_RECOMPUTE_ID`]).
async fn recorded_version(pool: &PgPool, marker_id: &str) -> Option<String> {
    sqlx::query_scalar("SELECT file_hash FROM keyword_sync_state WHERE id = $1")
        .bind(marker_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None)
}

/// Which keyword-blocked recompute a single-flight run targets.
#[derive(Clone, Copy)]
pub enum KwRecomputeKind {
    /// `media.is_keyword_blocked` from media-scoped keywords.
    Media,
    /// `stream.is_keyword_blocked` from stream-scoped keywords.
    Stream,
}

impl KwRecomputeKind {
    fn lease_id(self) -> &'static str {
        match self {
            Self::Media => MEDIA_KW_RECOMPUTE_LEASE_ID,
            Self::Stream => STREAM_KW_RECOMPUTE_LEASE_ID,
        }
    }
    fn marker_id(self) -> &'static str {
        match self {
            Self::Media => KW_BLOCKED_RECOMPUTE_ID,
            Self::Stream => STREAM_KW_BLOCKED_RECOMPUTE_ID,
        }
    }
    fn label(self) -> &'static str {
        match self {
            Self::Media => "keyword blocked recompute",
            Self::Stream => "stream keyword blocked recompute",
        }
    }
    fn version(self, kf: &KeywordFilterCache) -> u64 {
        match self {
            Self::Media => kf.media_version_tag(),
            // Full tag: any keyword/whitelist change (including media-scope
            // changes that may have crossover with stream scope) triggers a
            // stream recompute.
            Self::Stream => kf.version_tag(),
        }
    }
    /// Run the full-table sweep. Returns `true` only when every batch
    /// succeeded; marker publication is the caller's (fenced) responsibility.
    async fn sweep(self, pool: &PgPool, kf: &KeywordFilterCache) -> bool {
        match self {
            Self::Media => recompute_keyword_blocked(pool, &kf.keywords, &kf.whitelist).await,
            Self::Stream => {
                recompute_stream_keyword_blocked(pool, &kf.stream_keywords, &kf.whitelist).await
            }
        }
    }
}

/// Converge `kind`'s `is_keyword_blocked` column to the CURRENT keyword state
/// under a deployment-wide lease. This is the only entry point allowed to run
/// full-table sweeps — every trigger (startup version check, keyword file
/// sync, admin keyword edits) routes through here.
///
/// Loop semantics (every statement is single-statement autocommit, so this is
/// safe behind transaction-mode PgBouncer):
/// * Each attempt reloads the keyword state from the DB and targets THAT
///   version. While we waited, another pod (possibly running a newer image)
///   may have changed the keywords: chasing the live version means an
///   old-image pod can never downgrade the flags after a newer pod's sweep —
///   both converge to whatever the DB says now.
/// * If the completion marker already records the current version, done.
///   This is rechecked after every claim, so a contender that wins the lease
///   after the previous holder completed the same version exits immediately.
/// * The lease claim is an atomic upsert that takes the lease only when
///   absent or stale. The row stores a unique owner token; renewal and
///   release are fenced on it, so a preempted holder can neither refresh nor
///   delete a successor's lease.
/// * While sweeping, the lease is renewed every [`RECOMPUTE_LEASE_RENEW_SECS`].
///   If renewal fences out (0 rows), ownership was lost — abort the sweep. If
///   renewals keep failing, abort once [`RECOMPUTE_RENEW_ABORT_SECS`] pass
///   without a confirmed renewal — BEFORE the lease can go stale under us —
///   so two sweeps never overlap and a stale holder can never overwrite a
///   successor's completion marker.
/// * A finished sweep is verified against the marker (the sweep records it
///   internally only on full success); on any failure we back off before
///   retrying rather than immediately rescanning.
pub async fn kw_recompute_single_flight(pool: &PgPool, kind: KwRecomputeKind) {
    let label = kind.label();
    // Unique owner token for lease fencing: pod hostname + wall-clock nanos.
    let owner = format!(
        "{}:{}",
        std::env::var("HOSTNAME").unwrap_or_else(|_| "unknown".into()),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    );

    for attempt in 1..=RECOMPUTE_MAX_ATTEMPTS {
        // Fresh truth each attempt — see doc comment. Must be the fallible
        // load: a transient read failure silently coerced to "no keywords"
        // would clear every blocked flag and record a fabricated version.
        let kf = match try_load_keyword_filter_cache(pool).await {
            Ok(kf) => kf,
            Err(e) => {
                tracing::warn!("{label}: keyword state load failed, retrying: {e}");
                tokio::time::sleep(std::time::Duration::from_secs(RECOMPUTE_RETRY_SLEEP_SECS))
                    .await;
                continue;
            }
        };
        let ver = kind.version(&kf);
        let ver_str = format!("{ver:016x}");

        if recorded_version(pool, kind.marker_id()).await.as_deref() == Some(ver_str.as_str()) {
            tracing::debug!("{label}: version {ver_str} already recorded, done");
            return;
        }

        // Atomic claim: insert the lease row, or take it over only when stale.
        // Exactly one contender gets a row back per stale-window.
        let claimed: Option<String> = sqlx::query_scalar(
            "INSERT INTO keyword_sync_state (id, file_hash, synced_at)
             VALUES ($1, $2, NOW())
             ON CONFLICT (id) DO UPDATE
               SET file_hash = EXCLUDED.file_hash, synced_at = NOW()
               WHERE keyword_sync_state.synced_at < NOW() - ($3 * interval '1 second')
             RETURNING id",
        )
        .bind(kind.lease_id())
        .bind(&owner)
        .bind(RECOMPUTE_LEASE_STALE_SECS)
        .fetch_optional(pool)
        .await
        .unwrap_or_else(|e| {
            tracing::error!("{label}: lease claim failed: {e}");
            None
        });

        if claimed.is_none() {
            tracing::debug!(
                "{label}: another pod holds the recompute lease (attempt {attempt}), waiting"
            );
            tokio::time::sleep(std::time::Duration::from_secs(RECOMPUTE_RETRY_SLEEP_SECS)).await;
            continue;
        }

        // We hold the lease. Recheck the marker: the previous holder may have
        // completed this very version between our check above and the claim.
        if recorded_version(pool, kind.marker_id()).await.as_deref() == Some(ver_str.as_str()) {
            tracing::debug!("{label}: version {ver_str} completed while claiming, done");
            release_lease(pool, kind.lease_id(), &owner, label).await;
            return;
        }

        // Confirm ownership with a fenced renewal immediately before starting
        // the sweep: if this task was paused/descheduled long enough after the
        // claim for the lease to go stale, a successor may already own it.
        if !renew_lease_fenced(pool, kind.lease_id(), &owner, label).await {
            tracing::warn!("{label}: lease lost before sweep start, retrying");
            tokio::time::sleep(std::time::Duration::from_secs(RECOMPUTE_RETRY_SLEEP_SECS)).await;
            continue;
        }

        // Renew (owner-fenced) while the sweep runs. Returns only on ownership
        // loss or renewal-deadline breach, which aborts the sweep via select!.
        let renew_until_lost = async {
            let mut tick = tokio::time::interval(std::time::Duration::from_secs(
                RECOMPUTE_LEASE_RENEW_SECS,
            ));
            // The interval's first tick is immediate, so the first loop
            // iteration performs a real fenced renewal right away — if the
            // task was paused between claim and here long enough to lose the
            // lease, the sweep is aborted at once, not 60s later.
            let mut last_confirmed = std::time::Instant::now();
            loop {
                tick.tick().await;
                // Bound the whole renewal attempt (pool acquire included) by
                // the remaining abort budget: an unbounded await here could
                // block past our own lease expiry while the sweep keeps
                // running, letting a successor claim and overlap us.
                let remaining = RECOMPUTE_RENEW_ABORT_SECS
                    .saturating_sub(last_confirmed.elapsed().as_secs())
                    .max(1);
                let renewal = sqlx::query(
                    "UPDATE keyword_sync_state SET synced_at = NOW() WHERE id = $1 AND file_hash = $2",
                )
                .bind(kind.lease_id())
                .bind(&owner)
                .execute(pool);
                match tokio::time::timeout(std::time::Duration::from_secs(remaining), renewal)
                    .await
                {
                    Ok(Ok(r)) if r.rows_affected() == 0 => {
                        tracing::warn!("{label}: lease ownership lost, aborting this sweep");
                        return;
                    }
                    Ok(Ok(_)) => last_confirmed = std::time::Instant::now(),
                    Ok(Err(e)) => tracing::warn!("{label}: lease renewal error: {e}"),
                    Err(_) => tracing::warn!("{label}: lease renewal timed out after {remaining}s"),
                }
                // If we cannot CONFIRM a renewal for long enough that the lease
                // could go stale under us, abort before a successor can claim —
                // never run past our own lease.
                if last_confirmed.elapsed().as_secs() >= RECOMPUTE_RENEW_ABORT_SECS {
                    tracing::warn!(
                        "{label}: no confirmed lease renewal for {RECOMPUTE_RENEW_ABORT_SECS}s, \
                         aborting this sweep before the lease expires"
                    );
                    return;
                }
            }
        };

        let swept_ok = tokio::select! {
            ok = kind.sweep(pool, &kf) => ok,
            _ = renew_until_lost => false,
        };

        // Publish the completion marker ONLY through an atomic owner-fenced
        // statement: a holder that silently lost its lease (runtime pause,
        // renewal stall) cannot publish at all, so it can never overwrite a
        // successor's completion state. `published` implies we owned the lease
        // at the instant of publication.
        let published = swept_ok
            && publish_marker_fenced(
                pool,
                kind.marker_id(),
                &ver_str,
                kind.lease_id(),
                &owner,
                label,
            )
            .await;

        release_lease(pool, kind.lease_id(), &owner, label).await;

        if published {
            // Swept and published — but do NOT return yet: the keywords may
            // have changed again mid-sweep (e.g. an admin edit reverted to a
            // previously recorded version, which a fresh contender would see
            // as "already recorded" and skip). Loop once more: the top-of-loop
            // reload compares the marker against the LIVE keyword state and
            // exits only when they agree, otherwise we keep converging.
            tracing::info!("{label}: swept version {ver_str}, reverifying against live state");
            continue;
        }
        if swept_ok {
            tracing::warn!(
                "{label}: sweep finished but lease ownership could not be confirmed at \
                 publication, retrying to reverify"
            );
        } else {
            // Sweep failed (batch error) or was aborted on ownership loss.
            tracing::warn!("{label}: sweep did not complete for version {ver_str}, backing off");
        }
        tokio::time::sleep(std::time::Duration::from_secs(RECOMPUTE_RETRY_SLEEP_SECS)).await;
    }
    tracing::error!(
        "{label}: giving up after {RECOMPUTE_MAX_ATTEMPTS} attempts; will retry on next pod start"
    );
}

/// Atomically publish the completion marker, fenced on lease ownership: the
/// upsert proposes a row only when the lease row still carries our owner
/// token, all in one statement (transaction-pooling safe). Returns `true`
/// only when the marker was actually written — i.e. we owned the lease at the
/// instant of publication. A holder that lost its lease cannot publish.
async fn publish_marker_fenced(
    pool: &PgPool,
    marker_id: &str,
    ver_str: &str,
    lease_id: &str,
    owner: &str,
    label: &str,
) -> bool {
    match sqlx::query(
        "INSERT INTO keyword_sync_state (id, file_hash, synced_at)
         SELECT $1, $2, NOW()
         WHERE EXISTS (
             SELECT 1 FROM keyword_sync_state WHERE id = $3 AND file_hash = $4
         )
         ON CONFLICT (id) DO UPDATE
           SET file_hash = EXCLUDED.file_hash, synced_at = NOW()",
    )
    .bind(marker_id)
    .bind(ver_str)
    .bind(lease_id)
    .bind(owner)
    .execute(pool)
    .await
    {
        Ok(r) => r.rows_affected() > 0,
        Err(e) => {
            tracing::error!("{label}: fenced marker publication failed: {e}");
            false
        }
    }
}

/// Owner-fenced lease renewal. Returns `true` only when we still own the
/// lease (the fenced UPDATE touched the row). Errors count as "not confirmed"
/// — callers must treat that as possible ownership loss.
async fn renew_lease_fenced(pool: &PgPool, lease_id: &str, owner: &str, label: &str) -> bool {
    match sqlx::query(
        "UPDATE keyword_sync_state SET synced_at = NOW() WHERE id = $1 AND file_hash = $2",
    )
    .bind(lease_id)
    .bind(owner)
    .execute(pool)
    .await
    {
        Ok(r) => r.rows_affected() > 0,
        Err(e) => {
            tracing::warn!("{label}: fenced lease renewal failed: {e}");
            false
        }
    }
}

/// Delete the lease row, fenced on the owner token so a preempted holder can
/// never remove a successor's lease.
async fn release_lease(pool: &PgPool, lease_id: &str, owner: &str, label: &str) {
    if let Err(e) = sqlx::query("DELETE FROM keyword_sync_state WHERE id = $1 AND file_hash = $2")
        .bind(lease_id)
        .bind(owner)
        .execute(pool)
        .await
    {
        tracing::warn!("{label}: failed to release lease (expires in {RECOMPUTE_LEASE_STALE_SECS}s): {e}");
    }
}

/// Batch-recompute `stream.is_keyword_blocked` for all rows.
///
/// Mirrors `recompute_keyword_blocked` but targets the `stream` table using
/// stream-scoped keywords (scope 'stream' or 'all').
async fn recompute_stream_keyword_blocked(
    pool: &PgPool,
    stream_keywords: &[String],
    whitelist: &[String],
) -> bool {
    let kw_pattern: Option<String> = build_regex_pattern(stream_keywords);
    let wl_pattern: Option<String> = build_regex_pattern(whitelist);

    // Get the current max ID so we know when to stop.
    let max_id: i32 = match sqlx::query_scalar::<_, Option<i32>>("SELECT MAX(id) FROM stream")
        .fetch_one(pool)
        .await
    {
        Ok(Some(id)) => id,
        Ok(None) => {
            // An empty table is trivially converged.
            tracing::debug!("stream keyword blocked: stream table is empty, nothing to sweep");
            return true;
        }
        Err(e) => {
            tracing::error!("recompute_stream_keyword_blocked failed to get max id: {e}");
            return false;
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
                    return false;
                }
            }
        }

        from_id = to_id;
        if from_id >= max_id {
            break;
        }
    }

    // Completion-marker publication lives in kw_recompute_single_flight, fenced
    // on lease ownership — a sweep that lost its lease must not publish.
    let elapsed = started.elapsed();
    tracing::info!(
        keywords = stream_keywords.len(),
        updated = total_updated,
        elapsed_ms = elapsed.as_millis(),
        "keyword filter: stream is_keyword_blocked recompute complete"
    );
    true
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
    tokio::spawn(
        async move { kw_recompute_single_flight(&pool, KwRecomputeKind::Stream).await },
    );
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
    tokio::spawn(async move { kw_recompute_single_flight(&pool, KwRecomputeKind::Media).await });
}

pub async fn load_keyword_filter_cache(pool: &PgPool) -> KeywordFilterCache {
    // In-memory filtering degrades gracefully to "no filters" on a transient
    // read failure; the DB-column sweeps must NOT (see
    // try_load_keyword_filter_cache) — an empty state there would clear every
    // blocked flag and record a fabricated version.
    try_load_keyword_filter_cache(pool)
        .await
        .unwrap_or_else(|e| {
            tracing::error!("keyword filter cache load failed, using empty filters: {e}");
            KeywordFilterCache {
                keywords: vec![],
                stream_keywords: vec![],
                whitelist: vec![],
                nsfw_filter_enabled: false,
            }
        })
}

/// Fallible variant of [`load_keyword_filter_cache`], for callers that must
/// distinguish "no keywords configured" from "could not read the keywords"
/// (the single-flight sweeps: sweeping with silently-empty state would clear
/// every `is_keyword_blocked` flag and record a fabricated version).
pub async fn try_load_keyword_filter_cache(
    pool: &PgPool,
) -> Result<KeywordFilterCache, sqlx::Error> {
    let keywords: Vec<String> = sqlx::query_scalar(
        "SELECT LOWER(keyword) FROM keyword_filters WHERE is_active = true AND scope IN ('all', 'media') ORDER BY keyword",
    )
    .fetch_all(pool)
    .await?;

    let stream_keywords: Vec<String> = sqlx::query_scalar(
        "SELECT LOWER(keyword) FROM keyword_filters WHERE is_active = true AND scope IN ('all', 'stream') ORDER BY keyword",
    )
    .fetch_all(pool)
    .await?;

    let whitelist: Vec<String> =
        sqlx::query_scalar("SELECT LOWER(phrase) FROM keyword_whitelist ORDER BY phrase")
            .fetch_all(pool)
            .await?;
    Ok(KeywordFilterCache {
        keywords,
        stream_keywords,
        whitelist,
        nsfw_filter_enabled: false, // caller sets this from config after loading
    })
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

    // Converge the media flags in the background via the deployment-wide
    // single-flight lease (which reloads the merged file+admin keyword state
    // from the DB — computing a version from the file lists alone here would
    // disagree with the DB-derived tag whenever admin-managed rows exist).
    {
        let pool = pool.clone();
        tokio::spawn(
            async move { kw_recompute_single_flight(&pool, KwRecomputeKind::Media).await },
        );
    }

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
    // Converge the stream flags in the background via the deployment-wide
    // single-flight lease (see the media sync above for why the version must
    // come from the DB, not the file lists).
    {
        let pool = pool.clone();
        tokio::spawn(
            async move { kw_recompute_single_flight(&pool, KwRecomputeKind::Stream).await },
        );
    }

    tracing::info!(
        "stream keyword sync: done — {} keywords (hash {hash})",
        stream_keywords.len()
    );
}
