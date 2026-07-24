use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use fred::prelude::*;
use tokio::task::JoinSet;

use crate::{
    config::AppConfig,
    models::user_data::UserData,
    parser,
    scrapers::{
        ScrapedStream, ScrapedUsenetStream, SearchMeta, easynews, jackett, mediafusion, newznab,
        prowlarr, public_indexers, public_usenet,
        source_health::{self, HealthGateConfig},
        stream_convert, telegram, title_queries, torbox_search, torrentio, torznab, zilean,
    },
    state::AppState,
};

/// Core validation shared by all stream types (torrent, usenet, telegram).
/// Checks title similarity, year match for movies, and S/E presence for series.
fn validate_stream_core(
    parsed: &crate::parser::ParsedTitle,
    files: &[crate::scrapers::StreamFile],
    raw_name: &str,
    meta: &SearchMeta,
    media_type: &str,
    cfg: &AppConfig,
) -> bool {
    let sim_min = if media_type == "movie" {
        cfg.movie_similarity_min
    } else {
        cfg.series_similarity_min
    };
    let parsed_title = parsed.title.as_deref().unwrap_or(raw_name);
    if parser::similarity_ratio(parsed_title, &meta.title) < sim_min {
        return false;
    }
    if media_type == "movie"
        && let (Some(py), Some(my)) = (parsed.year, meta.year)
        && py != my
    {
        return false;
    }
    if media_type == "series" && files.is_empty() {
        return false;
    }
    true
}

fn validate_scraped_stream(stream: &ScrapedStream, meta: &SearchMeta, media_type: &str, cfg: &AppConfig) -> bool {
    validate_stream_core(
        &stream.parsed,
        &stream.files,
        &stream.name,
        meta,
        media_type,
        cfg,
    )
}

fn validate_usenet_stream(
    stream: &ScrapedUsenetStream,
    meta: &SearchMeta,
    media_type: &str,
    cfg: &AppConfig,
) -> bool {
    validate_stream_core(
        &stream.parsed,
        &stream.files,
        &stream.name,
        meta,
        media_type,
        cfg,
    )
}

fn validate_telegram_stream(
    stream: &crate::scrapers::ScrapedTelegramStream,
    meta: &SearchMeta,
    media_type: &str,
    cfg: &AppConfig,
) -> bool {
    let sim_min = if media_type == "movie" {
        cfg.movie_similarity_min
    } else {
        cfg.series_similarity_min
    };
    let parsed_title = stream.parsed.title.as_deref().unwrap_or(&stream.name);
    if parser::similarity_ratio(parsed_title, &meta.title) < sim_min {
        return false;
    }
    if media_type == "movie"
        && let (Some(py), Some(my)) = (stream.parsed.year, meta.year)
        && py != my
    {
        return false;
    }
    if media_type == "series" && stream.season.is_none() {
        return false;
    }
    true
}

pub(crate) struct FanOutOpts {
    pub byparr_url: Option<String>,
    pub bypass_ttl: bool,
    pub max_process_override: Option<(usize, Duration)>,
    pub query_timeout_override: Option<Duration>,
    pub title_search: bool,
}

fn live_fan_out_opts(cfg: &crate::config::AppConfig) -> FanOutOpts {
    FanOutOpts {
        byparr_url: None,
        bypass_ttl: false,
        max_process_override: None,
        query_timeout_override: None,
        title_search: cfg.prowlarr_live_title_search || cfg.jackett_live_title_search,
    }
}

fn background_fan_out_opts(cfg: &crate::config::AppConfig) -> FanOutOpts {
    FanOutOpts {
        byparr_url: cfg.byparr_url.clone(),
        bypass_ttl: true,
        max_process_override: Some((
            cfg.background_max_process,
            Duration::from_secs(cfg.background_max_process_time),
        )),
        query_timeout_override: Some(Duration::from_secs(cfg.background_query_timeout)),
        title_search: true,
    }
}

fn build_title_queries(
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<String> {
    match (media_type, season, episode) {
        ("series", Some(s), Some(e)) => title_queries::series_title_queries(&meta.title, s, e),
        ("movie", _, _) => title_queries::movie_title_queries(&meta.title, meta.year),
        _ => Vec::new(),
    }
}

/// Run torrent + usenet live scrapers for the given media item.
///
/// Both paths share a single Redis lock so a warm follow-up request does not
/// re-run user-credential scrapers (Easynews, TorBox usenet) when torrent
/// fan-out was already skipped due to the cooldown window.
pub async fn run_live_search(
    state: &AppState,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    scope: &str,
) -> (Vec<ScrapedStream>, Vec<ScrapedUsenetStream>) {
    let lock_key = lock_key(meta, season, episode);
    match try_acquire_lock(&state.redis, &lock_key).await {
        Ok(true) => {}
        Ok(false) => {
            tracing::debug!("orchestrator: lock held, skipping live scrape for {lock_key}");
            return (vec![], vec![]);
        }
        Err(e) => {
            tracing::warn!("orchestrator: redis lock error ({e}), proceeding anyway");
        }
    }

    let (torrents, usenet) = tokio::join!(
        run_torrent_scrape(state, user_data, meta, media_type, season, episode),
        run_usenet(
            state, user_data, meta, media_type, season, episode, scope, false,
        ),
    );

    (torrents, usenet)
}

/// Run all live torrent scrapers for the given media item.
///
/// Returns immediately with an empty vec if:
/// - A concurrent request already holds the Redis lock for this item
pub async fn run(
    state: &AppState,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    _scope: &str,
) -> Vec<ScrapedStream> {
    let lock_key = lock_key(meta, season, episode);
    match try_acquire_lock(&state.redis, &lock_key).await {
        Ok(true) => {}
        Ok(false) => {
            tracing::debug!("orchestrator: lock held, skipping live scrape for {lock_key}");
            return vec![];
        }
        Err(e) => {
            tracing::warn!("orchestrator: redis lock error ({e}), proceeding anyway");
        }
    }

    run_torrent_scrape(state, user_data, meta, media_type, season, episode).await
}

async fn run_torrent_scrape(
    state: &AppState,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedStream> {
    let results = fan_out_with_opts(
        state,
        user_data,
        meta,
        media_type,
        season,
        episode,
        &state.redis,
        &live_fan_out_opts(&state.config),
    )
    .await;

    // Deduplicate by info_hash (first occurrence wins).
    let mut seen = std::collections::HashSet::new();
    let deduped: Vec<ScrapedStream> = results
        .into_iter()
        .filter(|s| validate_scraped_stream(s, meta, media_type, &state.config))
        .filter(|s| seen.insert(s.info_hash.clone()))
        .collect();

    // Persist scraped results to DB. Do NOT invalidate the stream cache here —
    // invalidating on every scrape forces the next request to cold-path through
    // DB and re-run scrapers, turning every "warm" request into a slow one.
    // The lock TTL (300s) serves as the cooldown window; the cache is refreshed
    // naturally when the next cold-path request repopulates it.
    let opts = stream_convert::scraper_store_opts(meta.media_id, media_type, season, episode);
    let normalized: Vec<_> = deduped
        .iter()
        .map(crate::db::TorrentStoreInput::from)
        .collect();
    crate::db::store_torrent_streams(&state.pool, &normalized, &opts).await;

    // ── Telegram live scraper (Phase 2c) ─────────────────────────────────────
    if let Some(ref tg_client) = state.telegram {
        let user_channel_list = if let Some(uid) = user_data.user_id {
            crate::db::telegram_channels::user_scraping_channels(&state.pool, uid).await
        } else {
            vec![]
        };
        let kf_tg = state
            .keyword_filters
            .read()
            .map(|g| g.clone())
            .unwrap_or_default();
        let tg_results = telegram::scrape(
            tg_client,
            &state.config.telegram_scraping_channels,
            &user_channel_list,
            meta,
            media_type,
            season,
            episode,
            state.config.telegram_scrape_message_limit,
            state.config.min_scraping_video_size,
            &kf_tg,
        )
        .await;

        let tg_opts =
            stream_convert::scraper_store_opts(meta.media_id, media_type, season, episode);
        let tg_validated: Vec<_> = tg_results
            .into_iter()
            .filter(|s| validate_telegram_stream(s, meta, media_type, &state.config))
            .collect();
        let tg_normalized: Vec<_> = tg_validated
            .iter()
            .map(crate::db::TelegramStoreInput::from)
            .collect();
        crate::db::store_telegram_streams(&state.pool, &tg_normalized, &tg_opts).await;
    }

    // Do NOT delete the lock — let it expire after 300s. This gives a 5-minute
    // cooldown before scrapers run again for the same item, preventing every
    // warm request from paying the full scraping cost.

    deduped
}

/// Run all scrapers unconditionally (for on-demand user-triggered scrapes).
///
/// Unlike `run`, this bypasses the `live_search_streams` flag — it is intended for
/// explicit scrape requests, not the passive live-search path on stream resolution.
pub async fn run_forced(
    state: &AppState,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    scope: &str,
) -> Vec<ScrapedStream> {
    let lock_key = lock_key(meta, season, episode);
    match try_acquire_lock(&state.redis, &lock_key).await {
        Ok(true) => {}
        Ok(false) => {
            tracing::debug!("orchestrator: lock held, skipping forced scrape for {lock_key}");
            return vec![];
        }
        Err(e) => {
            tracing::warn!("orchestrator: redis lock error ({e}), proceeding anyway");
        }
    }

    let results = fan_out_with_opts(
        state,
        user_data,
        meta,
        media_type,
        season,
        episode,
        &state.redis,
        &live_fan_out_opts(&state.config),
    )
    .await;

    let mut seen = std::collections::HashSet::new();
    let deduped: Vec<ScrapedStream> = results
        .into_iter()
        .filter(|s| validate_scraped_stream(s, meta, media_type, &state.config))
        .filter(|s| seen.insert(s.info_hash.clone()))
        .collect();

    let opts = stream_convert::scraper_store_opts(meta.media_id, media_type, season, episode);
    let normalized: Vec<_> = deduped
        .iter()
        .map(crate::db::TorrentStoreInput::from)
        .collect();
    crate::db::store_torrent_streams(&state.pool, &normalized, &opts).await;
    // Forced scrape explicitly invalidates the cache so the caller sees fresh results.
    invalidate_stream_cache(&state.redis, meta, media_type, season, episode, scope).await;

    if let Some(ref tg_client) = state.telegram {
        let user_channel_list = if let Some(uid) = user_data.user_id {
            crate::db::telegram_channels::user_scraping_channels(&state.pool, uid).await
        } else {
            vec![]
        };
        let kf_tg2 = state
            .keyword_filters
            .read()
            .map(|g| g.clone())
            .unwrap_or_default();
        let tg_results = telegram::scrape(
            tg_client,
            &state.config.telegram_scraping_channels,
            &user_channel_list,
            meta,
            media_type,
            season,
            episode,
            state.config.telegram_scrape_message_limit,
            state.config.min_scraping_video_size,
            &kf_tg2,
        )
        .await;
        let tg_opts =
            stream_convert::scraper_store_opts(meta.media_id, media_type, season, episode);
        let tg_validated: Vec<_> = tg_results
            .into_iter()
            .filter(|s| validate_telegram_stream(s, meta, media_type, &state.config))
            .collect();
        let tg_normalized: Vec<_> = tg_validated
            .iter()
            .map(crate::db::TelegramStoreInput::from)
            .collect();
        crate::db::store_telegram_streams(&state.pool, &tg_normalized, &tg_opts).await;
    }

    // Release the lock so a subsequent forced scrape can run immediately.
    let _: Result<(), _> = state.redis.del(&lock_key).await;
    deduped
}

/// Run usenet scrapers (Easynews + TorBox usenet) for the given media item.
///
/// Separate from torrent fan-out so the kodi_stream route (torrents-only) is unaffected.
/// Per-scraper Redis TTLs mirror Python's `BaseScraper.cache` — timestamps are recorded
/// even when a scraper returns no results (e.g. bad credentials or rate limits).
pub async fn run_usenet(
    state: &AppState,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    scope: &str,
    bypass_ttl: bool,
) -> Vec<ScrapedUsenetStream> {
    let _ = scope;
    let cfg = &state.config;
    let kf = state
        .keyword_filters
        .read()
        .map(|g| g.clone())
        .unwrap_or_default();
    let cache_key = media_cache_key(meta, season, episode);
    let now = scrape_timestamp_now();
    let last_scraped = fetch_last_scraped(
        &state.redis,
        &[
            "easynews",
            "torbox_search",
            "newznab",
            "public_usenet_indexers",
        ],
        &cache_key,
    )
    .await;
    let is_stale = |scraper_id: &str, ttl: i64| {
        is_scrape_stale(&last_scraped, scraper_id, ttl, now, bypass_ttl)
    };

    let mut results: Vec<ScrapedUsenetStream> = Vec::new();
    let mut scraped_ids: Vec<&str> = Vec::new();

    // ── Easynews ─────────────────────────────────────────────────────────────
    // Credentials come from the user's streaming provider config, not server config.
    if is_stale("easynews", cfg.prowlarr_search_ttl)
        && let Some(en_provider) = user_data
            .streaming_providers
            .iter()
            .find(|p| p.service == "easynews" && p.enabled)
    {
        let username = en_provider
            .email
            .as_deref()
            .filter(|s| !s.is_empty())
            .or_else(|| {
                en_provider
                    .easynews_config
                    .as_ref()
                    .and_then(|c| c.get("username").or_else(|| c.get("email")))
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
            });
        let password = en_provider
            .password
            .as_deref()
            .filter(|s| !s.is_empty())
            .or_else(|| {
                en_provider
                    .easynews_config
                    .as_ref()
                    .and_then(|c| c.get("password"))
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
            });
        if let (Some(u), Some(p)) = (username, password) {
            let en = easynews::scrape_with_credentials(
                &state.http,
                u,
                p,
                meta,
                media_type,
                season,
                episode,
                &kf,
            )
            .await;
            results.extend(en);
            scraped_ids.push("easynews");
        }
    }

    // ── TorBox Usenet ─────────────────────────────────────────────────────────
    if is_stale("torbox_search", cfg.torbox_search_ttl) && torbox_search::has_token(user_data) {
        let tb = torbox_search::scrape_usenet(
            &state.http,
            user_data,
            meta,
            media_type,
            season,
            episode,
            &kf,
        )
        .await;
        results.extend(tb);
        scraped_ids.push("torbox_search");
    }

    // ── User-configured Newznab indexers ──────────────────────────────────────
    if is_stale("newznab", cfg.prowlarr_search_ttl) {
        let newznab_idxs: Vec<_> = user_data
            .indexer_config
            .as_ref()
            .map(|ic| {
                ic.newznab_indexers
                    .iter()
                    .filter(|n| n.enabled)
                    .cloned()
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        if !newznab_idxs.is_empty() {
            let nz = newznab::scrape(
                &state.http,
                &newznab_idxs,
                meta,
                media_type,
                season,
                episode,
                &kf,
            )
            .await;
            results.extend(nz);
            scraped_ids.push("newznab");
        }
    }

    // ── Public Usenet indexers (NZBIndex + Binsearch) ─────────────────────────
    if cfg.is_scrap_from_public_usenet_indexers
        && is_stale("public_usenet_indexers", cfg.public_usenet_search_ttl)
    {
        let usenet_hg = HealthGateConfig {
            redis: state.redis.clone(),
            enabled: cfg.public_indexers_source_health_gates_enabled,
            min_samples: cfg.public_indexers_source_health_min_samples,
            min_success_rate: cfg.public_indexers_source_min_success_rate,
            max_timeout_rate: cfg.public_indexers_source_max_timeout_rate,
            counter_soft_cap: cfg.public_indexers_source_health_counter_soft_cap,
            decay_factor: cfg.public_indexers_source_health_decay_factor,
            recovery_success_streak: cfg.public_indexers_source_health_recovery_success_streak,
            scope_mode: cfg.public_indexers_source_health_scope_mode.clone(),
            scope_override: cfg.public_indexers_source_health_scope.clone(),
            metrics_ttl_seconds: cfg.public_indexers_source_health_metrics_ttl_seconds,
        };
        let pu = public_usenet::scrape(
            &state.http,
            meta,
            media_type,
            season,
            episode,
            Some(&usenet_hg),
            &kf,
            cfg,
        )
        .await;
        results.extend(pu);
        scraped_ids.push("public_usenet_indexers");
    }

    record_scrape_timestamps(&state.redis, &scraped_ids, &cache_key, now).await;

    let mut seen = std::collections::HashSet::new();
    let validated: Vec<ScrapedUsenetStream> = results
        .into_iter()
        .filter(|s| validate_usenet_stream(s, meta, media_type, &state.config))
        .filter(|s| seen.insert(s.nzb_guid.clone()))
        .collect();

    let opts = stream_convert::scraper_store_opts(meta.media_id, media_type, season, episode);
    let normalized: Vec<_> = validated
        .iter()
        .map(crate::db::UsenetStoreInput::from)
        .collect();
    crate::db::store_usenet_streams(&state.pool, &normalized, &opts).await;

    validated
}

/// Full torrent + usenet fan-out for background worker re-scrapes.
///
/// Bypasses live-search TTL gates and Redis lock; uses deeper processing limits
/// and Byparr for public indexers. Invalidates stream cache when done.
pub async fn run_background(
    state: &AppState,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    scope: &str,
) {
    let opts = background_fan_out_opts(&state.config);
    let results = fan_out_with_opts(
        state,
        user_data,
        meta,
        media_type,
        season,
        episode,
        &state.redis,
        &opts,
    )
    .await;

    let mut seen = std::collections::HashSet::new();
    let deduped: Vec<ScrapedStream> = results
        .into_iter()
        .filter(|s| validate_scraped_stream(s, meta, media_type, &state.config))
        .filter(|s| seen.insert(s.info_hash.clone()))
        .collect();

    let opts = stream_convert::scraper_store_opts(meta.media_id, media_type, season, episode);
    let normalized: Vec<_> = deduped
        .iter()
        .map(crate::db::TorrentStoreInput::from)
        .collect();
    crate::db::store_torrent_streams(&state.pool, &normalized, &opts).await;
    run_usenet(
        state, user_data, meta, media_type, season, episode, scope, true,
    )
    .await;
    invalidate_stream_cache(&state.redis, meta, media_type, season, episode, scope).await;
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

fn media_cache_key(meta: &SearchMeta, season: Option<i32>, episode: Option<i32>) -> String {
    let fallback = meta.media_id.to_string();
    let id = meta.imdb_id.as_deref().unwrap_or(&fallback);
    match (season, episode) {
        (Some(s), Some(e)) => format!("series:{id}:{s}:{e}"),
        _ => format!("movie:{id}"),
    }
}

fn scrape_timestamp_now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as f64
}

fn is_scrape_stale(
    last_scraped: &std::collections::HashMap<String, f64>,
    scraper_id: &str,
    ttl: i64,
    now: f64,
    bypass_ttl: bool,
) -> bool {
    if bypass_ttl {
        return true;
    }
    match last_scraped.get(scraper_id) {
        Some(&ts) => (now - ts) as i64 >= ttl,
        None => true,
    }
}

async fn fetch_last_scraped(
    redis: &fred::clients::Client,
    scraper_ids: &[&str],
    cache_key: &str,
) -> std::collections::HashMap<String, f64> {
    use fred::prelude::SortedSetsInterface;

    let mut last_scraped = std::collections::HashMap::new();
    for id in scraper_ids {
        if let Ok(Some(score)) = redis.zscore::<Option<f64>, _, _>(*id, cache_key).await {
            last_scraped.insert(id.to_string(), score);
        }
    }
    last_scraped
}

async fn record_scrape_timestamps(
    redis: &fred::clients::Client,
    scraper_ids: &[&str],
    cache_key: &str,
    now: f64,
) {
    use fred::prelude::SortedSetsInterface;

    for scraper_id in scraper_ids {
        let _: Result<i64, _> = redis
            .zadd(*scraper_id, None, None, false, false, (now, cache_key))
            .await;
    }
}

fn lock_key(meta: &SearchMeta, season: Option<i32>, episode: Option<i32>) -> String {
    let id = meta.imdb_id.as_deref().unwrap_or_else(|| {
        // Fallback to media_id if no imdb_id
        Box::leak(meta.media_id.to_string().into_boxed_str())
    });
    match (season, episode) {
        (Some(s), Some(e)) => format!("live_search_lock:{id}:S{s}E{e}"),
        _ => format!("live_search_lock:{id}"),
    }
}

/// Delete the stream_data Redis key so the next request takes the cold path and
/// builds a fresh, fully-populated blob from DB (including the streams just persisted).
async fn invalidate_stream_cache(
    redis: &fred::clients::Client,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    scope: &str,
) {
    let key = match (media_type, season, episode) {
        ("series", Some(s), Some(e)) => {
            format!("stream_data:series:{}:{}:{}:{scope}", meta.media_id, s, e)
        }
        _ => format!("stream_data:movie:{}:{scope}", meta.media_id),
    };
    let _: Result<i64, _> = redis.del(&key).await;
}

async fn try_acquire_lock(
    redis: &fred::clients::Client,
    key: &str,
) -> Result<bool, fred::error::Error> {
    // SET key 1 NX EX 300
    let result: Option<String> = redis
        .set(
            key,
            "1",
            Some(Expiration::EX(300)),
            Some(SetOptions::NX),
            false,
        )
        .await?;
    Ok(result.is_some())
}

async fn fan_out_with_opts(
    state: &AppState,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    redis: &fred::clients::Client,
    opts: &FanOutOpts,
) -> Vec<ScrapedStream> {
    let http = state.http.clone();
    let cfg = state.config.clone();
    let meta = Arc::new(meta.clone());
    let ic = user_data.indexer_config.clone().unwrap_or_default();
    let kf = state
        .keyword_filters
        .read()
        .map(|g| g.clone())
        .unwrap_or_default();

    // Build cache key for TTL checking
    let cache_key = media_cache_key(&meta, season, episode);
    let now = scrape_timestamp_now();

    // Pre-fetch last-scrape timestamps for all enabled scrapers from Redis.
    let scraper_ids = [
        "prowlarr",
        "zilean",
        "torrentio",
        "mediafusion",
        "jackett",
        "torznab",
        "torbox_search",
        "public_indexers",
    ];
    let last_scraped = fetch_last_scraped(redis, &scraper_ids, &cache_key).await;

    let is_stale = |scraper_id: &str, ttl: i64| -> bool {
        is_scrape_stale(&last_scraped, scraper_id, ttl, now, opts.bypass_ttl)
    };

    let title_queries = if opts.title_search {
        build_title_queries(&meta, media_type, season, episode)
    } else {
        Vec::new()
    };
    let title_queries = Arc::new(title_queries);

    let (prowlarr_max_process, prowlarr_max_time, prowlarr_query_timeout) =
        if let Some((max_process, max_time)) = opts.max_process_override {
            (
                max_process,
                max_time,
                opts.query_timeout_override
                    .unwrap_or_else(|| Duration::from_secs(cfg.prowlarr_search_query_timeout)),
            )
        } else {
            (
                cfg.prowlarr_immediate_max_process,
                Duration::from_secs(cfg.prowlarr_immediate_max_process_time),
                Duration::from_secs(cfg.prowlarr_search_query_timeout),
            )
        };

    let (jackett_max_process, jackett_max_time, jackett_query_timeout) =
        if let Some((max_process, max_time)) = opts.max_process_override {
            (
                max_process,
                max_time,
                opts.query_timeout_override
                    .unwrap_or_else(|| Duration::from_secs(cfg.jackett_search_query_timeout)),
            )
        } else {
            (
                cfg.jackett_immediate_max_process,
                Duration::from_secs(cfg.jackett_immediate_max_process_time),
                Duration::from_secs(cfg.jackett_search_query_timeout),
            )
        };

    // Health gate config for public indexers
    let health_gate = HealthGateConfig {
        redis: redis.clone(),
        enabled: cfg.public_indexers_source_health_gates_enabled,
        min_samples: cfg.public_indexers_source_health_min_samples,
        min_success_rate: cfg.public_indexers_source_min_success_rate,
        max_timeout_rate: cfg.public_indexers_source_max_timeout_rate,
        counter_soft_cap: cfg.public_indexers_source_health_counter_soft_cap,
        decay_factor: cfg.public_indexers_source_health_decay_factor,
        recovery_success_streak: cfg.public_indexers_source_health_recovery_success_streak,
        scope_mode: cfg.public_indexers_source_health_scope_mode.clone(),
        scope_override: cfg.public_indexers_source_health_scope.clone(),
        metrics_ttl_seconds: cfg.public_indexers_source_health_metrics_ttl_seconds,
    };

    // JoinSet returns (scraper_id, streams, start_ts, duration_secs)
    let mut set: JoinSet<(&'static str, Vec<ScrapedStream>, chrono::DateTime<Utc>, f64)> =
        JoinSet::new();
    let mut spawned_scrapers: Vec<&'static str> = Vec::new();

    // ── Torrentio ──────────────────────────────────────────────────────────────
    if cfg.is_scrap_from_torrentio && is_stale("torrentio", cfg.torrentio_search_ttl) {
        let http = http.clone();
        let url = cfg.torrentio_url.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        set.spawn(async move {
            let start = Utc::now();
            let t = std::time::Instant::now();
            let streams = torrentio::scrape(&http, &url, &meta, &mt, season, episode).await;
            ("torrentio", streams, start, t.elapsed().as_secs_f64())
        });
        spawned_scrapers.push("torrentio");
    }

    // ── Prowlarr (global config, or user-overridden URL/key) ──────────────────
    if is_stale("prowlarr", cfg.prowlarr_search_ttl)
        && let Some((url, key)) =
            crate::scrapers::indexer_credentials::resolve_prowlarr_credentials(&ic, &cfg)
    {
        let indexers = prowlarr::list_healthy_indexers(&http, &url, &key).await;
        if !indexers.is_empty() {
            let privacy_by_id = prowlarr::fetch_indexer_privacy_map(&http, &url, &key).await;
            let privacy_by_id = Arc::new(privacy_by_id);
            let deadline = Arc::new(tokio::time::Instant::now() + prowlarr_max_time);
            for idx in indexers {
                let http = http.clone();
                let url = url.clone();
                let key = key.clone();
                let meta = meta.clone();
                let mt = media_type.to_string();
                let title_queries = title_queries.clone();
                let privacy_by_id = privacy_by_id.clone();
                let deadline = deadline.clone();
                let indexer_name = idx.name.clone();
                set.spawn(async move {
                    let start = Utc::now();
                    let t = std::time::Instant::now();
                    let streams = prowlarr::scrape_indexer(
                        &http,
                        &url,
                        &key,
                        &idx,
                        &meta,
                        &mt,
                        season,
                        episode,
                        prowlarr_max_process,
                        prowlarr_query_timeout,
                        title_queries.as_slice(),
                        &privacy_by_id,
                        *deadline,
                    )
                    .await;
                    tracing::debug!("prowlarr indexer {indexer_name}: {} streams", streams.len());
                    ("prowlarr", streams, start, t.elapsed().as_secs_f64())
                });
            }
            spawned_scrapers.push("prowlarr");
        }
    }

    // ── Jackett (global config, or user-overridden) ───────────────────────────
    if cfg.is_scrap_from_jackett
        && is_stale("jackett", cfg.jackett_search_ttl)
        && let Some((url, key)) =
            crate::scrapers::indexer_credentials::resolve_jackett_credentials(&ic, &cfg)
    {
        let indexers = jackett::list_healthy_indexers(&http, &url, &key).await;
        if !indexers.is_empty() {
            let deadline = Arc::new(tokio::time::Instant::now() + jackett_max_time);
            for idx in indexers {
                let http = http.clone();
                let url = url.clone();
                let key = key.clone();
                let meta = meta.clone();
                let mt = media_type.to_string();
                let title_queries = title_queries.clone();
                let deadline = deadline.clone();
                let indexer_name = idx.name.clone();
                set.spawn(async move {
                    let start = Utc::now();
                    let t = std::time::Instant::now();
                    let streams = jackett::scrape_indexer(
                        &http,
                        &url,
                        &key,
                        &idx,
                        &meta,
                        &mt,
                        season,
                        episode,
                        jackett_max_process,
                        jackett_query_timeout,
                        title_queries.as_slice(),
                        *deadline,
                    )
                    .await;
                    tracing::debug!("jackett indexer {indexer_name}: {} streams", streams.len());
                    ("jackett", streams, start, t.elapsed().as_secs_f64())
                });
            }
            spawned_scrapers.push("jackett");
        }
    }

    // ── Zilean (search + filtered endpoints in parallel) ──────────────────────
    if cfg.is_scrap_from_zilean && is_stale("zilean", cfg.zilean_search_ttl) {
        let url = cfg.zilean_url.clone();
        {
            let http = http.clone();
            let meta = meta.clone();
            let mt = media_type.to_string();
            let url = url.clone();
            let kf = kf.clone();
            set.spawn(async move {
                let start = Utc::now();
                let t = std::time::Instant::now();
                let streams =
                    zilean::scrape_search(&http, &url, &meta, &mt, season, episode, &kf).await;
                tracing::debug!("zilean search: {} streams", streams.len());
                ("zilean", streams, start, t.elapsed().as_secs_f64())
            });
        }
        {
            let http = http.clone();
            let meta = meta.clone();
            let mt = media_type.to_string();
            let url = url.clone();
            let kf = kf.clone();
            set.spawn(async move {
                let start = Utc::now();
                let t = std::time::Instant::now();
                let streams =
                    zilean::scrape_filtered(&http, &url, &meta, &mt, season, episode, &kf).await;
                tracing::debug!("zilean filtered: {} streams", streams.len());
                ("zilean", streams, start, t.elapsed().as_secs_f64())
            });
        }
        spawned_scrapers.push("zilean");
    }

    // ── User-configured Torznab endpoints ─────────────────────────────────────
    if cfg.is_scrap_from_torznab && is_stale("torznab", cfg.jackett_search_ttl) {
        let torznab_eps: Vec<_> = ic
            .torznab_endpoints
            .into_iter()
            .filter(|e| e.enabled)
            .collect();
        if !torznab_eps.is_empty() {
            let http = http.clone();
            let meta = meta.clone();
            let mt = media_type.to_string();
            let kf = kf.clone();
            set.spawn(async move {
                let start = Utc::now();
                let t = std::time::Instant::now();
                let streams =
                    torznab::scrape(&http, &torznab_eps, &meta, &mt, season, episode, &kf).await;
                ("torznab", streams, start, t.elapsed().as_secs_f64())
            });
            spawned_scrapers.push("torznab");
        }
    }

    // ── MediaFusion peer ──────────────────────────────────────────────────────
    if cfg.is_scrap_from_mediafusion && is_stale("mediafusion", cfg.mediafusion_search_ttl) {
        let url = cfg.mediafusion_url.clone();
        let secret_str = cfg.mediafusion_secret_str.clone();
        let http = http.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        set.spawn(async move {
            let start = Utc::now();
            let t = std::time::Instant::now();
            let streams = mediafusion::scrape(
                &http,
                &url,
                &meta,
                &mt,
                season,
                episode,
                secret_str.as_deref(),
            )
            .await;
            ("mediafusion", streams, start, t.elapsed().as_secs_f64())
        });
        spawned_scrapers.push("mediafusion");
    }

    // ── TorBox Search (user token) ────────────────────────────────────────────
    if is_stale("torbox_search", cfg.torbox_search_ttl) {
        let http = http.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        let ud = user_data.clone();
        let kf = kf.clone();
        set.spawn(async move {
            let start = Utc::now();
            let t = std::time::Instant::now();
            let streams = torbox_search::scrape(&http, &ud, &meta, &mt, season, episode, &kf).await;
            ("torbox_search", streams, start, t.elapsed().as_secs_f64())
        });
        spawned_scrapers.push("torbox_search");
    }

    // ── Public torrent indexers (multi-site, optional Byparr) ────────────────
    if cfg.is_scrap_from_public_indexers
        && is_stale("public_indexers", cfg.public_indexers_search_ttl)
    {
        let http = http.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        // CF bypass (byparr) must NOT run during live API requests — it launches
        // Chromium sessions and causes severe CPU/latency spikes. Background workers
        // pass the configured byparr URL via FanOutOpts.
        let byparr = opts.byparr_url.clone();
        let sites = cfg.public_indexers_live_search_sites.clone();
        let hg = health_gate.clone();
        let kf = kf.clone();
        set.spawn(async move {
            let start = Utc::now();
            let t = std::time::Instant::now();
            let streams = public_indexers::scrape(
                &http,
                &meta,
                &mt,
                season,
                episode,
                byparr.as_deref(),
                sites.as_deref(),
                Some(&hg),
                &kf,
                &cfg,
            )
            .await;
            ("public_indexers", streams, start, t.elapsed().as_secs_f64())
        });
        spawned_scrapers.push("public_indexers");
    }

    let mut all: Vec<ScrapedStream> = Vec::new();
    while let Some(res) = set.join_next().await {
        match res {
            Ok((id, streams, start_ts, duration_secs)) => {
                tracing::info!("scraper {id}: {} streams for {}", streams.len(), cache_key);
                let end_ts = Utc::now();
                let count = streams.len();
                all.extend(streams);

                // Save run metrics to Redis so the admin dashboard shows Rust scraper activity
                let redis_clone = redis.clone();
                let meta_clone = meta.clone();
                let id_owned = id.to_string();
                let dur = duration_secs;
                tokio::spawn(async move {
                    source_health::save_scraper_run_metrics(
                        &redis_clone,
                        &id_owned,
                        meta_clone.imdb_id.as_deref(),
                        &meta_clone.title,
                        season,
                        episode,
                        count,
                        count,
                        0,
                        0,
                        &std::collections::HashMap::new(),
                        &start_ts,
                        &end_ts,
                    )
                    .await;
                    let _ = dur; // ensure it's used
                });
            }
            Err(e) => tracing::warn!("orchestrator: scraper task panicked: {e}"),
        }
    }

    // Record scrape timestamps for all scrapers that ran.
    record_scrape_timestamps(redis, &spawned_scrapers, &cache_key, now).await;

    all
}
