use std::sync::Arc;

use chrono::Utc;
use fred::prelude::*;
use tokio::task::JoinSet;

use crate::{
    models::user_data::UserData,
    scrapers::{
        easynews, jackett, mediafusion, newznab, persist, prowlarr, public_indexers, public_usenet,
        source_health::{self, HealthGateConfig},
        telegram, torbox_search, torrentio, torznab, zilean, ScrapedStream, ScrapedUsenetStream,
        SearchMeta,
    },
    state::AppState,
};

/// Run all live scrapers for the given media item.
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
    // Acquire SETNX lock to prevent stampede from concurrent requests.
    let lock_key = lock_key(meta, season, episode);
    match try_acquire_lock(&state.redis, &lock_key).await {
        Ok(true) => {} // we own the lock
        Ok(false) => {
            tracing::debug!("orchestrator: lock held, skipping live scrape for {lock_key}");
            return vec![];
        }
        Err(e) => {
            tracing::warn!("orchestrator: redis lock error ({e}), proceeding anyway");
        }
    }

    let results = fan_out(
        state,
        user_data,
        meta,
        media_type,
        season,
        episode,
        &state.redis,
    )
    .await;

    // Deduplicate by info_hash (first occurrence wins).
    let mut seen = std::collections::HashSet::new();
    let deduped: Vec<ScrapedStream> = results
        .into_iter()
        .filter(|s| seen.insert(s.info_hash.clone()))
        .collect();

    // Persist scraped results to DB. Do NOT invalidate the stream cache here —
    // invalidating on every scrape forces the next request to cold-path through
    // DB and re-run scrapers, turning every "warm" request into a slow one.
    // The lock TTL (300s) serves as the cooldown window; the cache is refreshed
    // naturally when the next cold-path request repopulates it.
    persist::write_back(&deduped, &state.pool, meta, media_type, season, episode).await;

    // ── Telegram live scraper (Phase 2c) ─────────────────────────────────────
    if let Some(ref tg_client) = state.telegram {
        let user_channel_list = if let Some(uid) = user_data.user_id {
            crate::db::telegram_channels::user_scraping_channels(&state.pool, uid).await
        } else {
            vec![]
        };
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
        )
        .await;

        persist::write_telegram_streams(
            &tg_results,
            &state.pool,
            meta,
            media_type,
            season,
            episode,
        )
        .await;
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

    let results = fan_out(
        state,
        user_data,
        meta,
        media_type,
        season,
        episode,
        &state.redis,
    )
    .await;

    let mut seen = std::collections::HashSet::new();
    let deduped: Vec<ScrapedStream> = results
        .into_iter()
        .filter(|s| seen.insert(s.info_hash.clone()))
        .collect();

    persist::write_back(&deduped, &state.pool, meta, media_type, season, episode).await;
    // Forced scrape explicitly invalidates the cache so the caller sees fresh results.
    invalidate_stream_cache(&state.redis, meta, media_type, season, episode, scope).await;

    if let Some(ref tg_client) = state.telegram {
        let user_channel_list = if let Some(uid) = user_data.user_id {
            crate::db::telegram_channels::user_scraping_channels(&state.pool, uid).await
        } else {
            vec![]
        };
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
        )
        .await;
        persist::write_telegram_streams(
            &tg_results,
            &state.pool,
            meta,
            media_type,
            season,
            episode,
        )
        .await;
    }

    // Release the lock so a subsequent forced scrape can run immediately.
    let _: Result<(), _> = state.redis.del(&lock_key).await;
    deduped
}

/// Run usenet scrapers (Easynews + TorBox usenet) for the given media item.
///
/// Separate from `run` so the kodi_stream route (torrents-only) is unaffected.
pub async fn run_usenet(
    state: &AppState,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    scope: &str,
) -> Vec<ScrapedUsenetStream> {
    let mut results: Vec<ScrapedUsenetStream> = Vec::new();

    // ── Easynews ─────────────────────────────────────────────────────────────
    // Credentials come from the user's streaming provider config, not server config.
    if let Some(en_provider) = user_data
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
            )
            .await;
            results.extend(en);
        }
    }

    // ── TorBox Usenet ─────────────────────────────────────────────────────────
    let tb =
        torbox_search::scrape_usenet(&state.http, user_data, meta, media_type, season, episode)
            .await;
    results.extend(tb);

    // ── Public Usenet indexers (NZBIndex + Binsearch) ─────────────────────────
    if state.config.is_scrap_from_public_usenet_indexers {
        let cfg = &state.config;
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
        )
        .await;
        results.extend(pu);
    }

    // Persist to DB
    persist::write_back_usenet(&results, &state.pool, meta, media_type, season, episode).await;

    let _ = scope;
    results
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

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

async fn fan_out(
    state: &AppState,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    redis: &fred::clients::Client,
) -> Vec<ScrapedStream> {
    use fred::prelude::SortedSetsInterface;

    let http = state.http.clone();
    let cfg = state.config.clone();
    let meta = Arc::new(meta.clone());
    let ic = user_data.indexer_config.clone().unwrap_or_default();

    // Build cache key for TTL checking
    let cache_key = match (season, episode) {
        (Some(s), Some(e)) => format!(
            "series:{}:{}:{}",
            meta.imdb_id
                .as_deref()
                .unwrap_or(&meta.media_id.to_string()),
            s,
            e
        ),
        _ => format!(
            "movie:{}",
            meta.imdb_id
                .as_deref()
                .unwrap_or(&meta.media_id.to_string())
        ),
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as f64;

    // Pre-fetch last-scrape timestamps for all enabled scrapers from Redis.
    let scraper_ids = [
        "prowlarr",
        "zilean",
        "torrentio",
        "mediafusion",
        "jackett",
        "torznab",
        "torbox_search",
        "newznab",
        "public_indexers",
    ];
    let mut last_scraped: std::collections::HashMap<&str, f64> = std::collections::HashMap::new();
    for id in &scraper_ids {
        if let Ok(Some(score)) = redis
            .zscore::<Option<f64>, _, _>(*id, cache_key.as_str())
            .await
        {
            last_scraped.insert(id, score);
        }
    }

    let is_stale = |scraper_id: &str, ttl: i64| -> bool {
        match last_scraped.get(scraper_id) {
            Some(&ts) => (now - ts) as i64 >= ttl,
            None => true, // never scraped → stale → should scrape
        }
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
    if cfg.is_scrap_from_prowlarr
        && cfg.prowlarr_live_title_search
        && is_stale("prowlarr", cfg.prowlarr_search_ttl)
    {
        let prowlarr_url = ic
            .prowlarr
            .as_ref()
            .and_then(|p| if p.use_global { None } else { p.url.clone() })
            .or_else(|| cfg.prowlarr_url.clone());
        let prowlarr_key = ic
            .prowlarr
            .as_ref()
            .and_then(|p| {
                if p.use_global {
                    None
                } else {
                    p.api_key.clone()
                }
            })
            .or_else(|| cfg.prowlarr_api_key.clone());
        if let (Some(url), Some(key)) = (prowlarr_url, prowlarr_key) {
            let http = http.clone();
            let meta = meta.clone();
            let mt = media_type.to_string();
            let max_process = cfg.prowlarr_immediate_max_process;
            let max_process_time =
                std::time::Duration::from_secs(cfg.prowlarr_immediate_max_process_time);
            let query_timeout = std::time::Duration::from_secs(cfg.prowlarr_search_query_timeout);
            set.spawn(async move {
                let start = Utc::now();
                let t = std::time::Instant::now();
                let streams = prowlarr::scrape(
                    &http,
                    &url,
                    &key,
                    &meta,
                    &mt,
                    season,
                    episode,
                    max_process,
                    max_process_time,
                    query_timeout,
                )
                .await;
                ("prowlarr", streams, start, t.elapsed().as_secs_f64())
            });
            spawned_scrapers.push("prowlarr");
        }
    }

    // ── Jackett (global config, or user-overridden) ───────────────────────────
    if cfg.is_scrap_from_jackett && is_stale("jackett", cfg.jackett_search_ttl) {
        let jackett_url = ic
            .jackett
            .as_ref()
            .and_then(|j| if j.use_global { None } else { j.url.clone() })
            .or_else(|| cfg.jackett_url.clone());
        let jackett_key = ic
            .jackett
            .as_ref()
            .and_then(|j| {
                if j.use_global {
                    None
                } else {
                    j.api_key.clone()
                }
            })
            .or_else(|| cfg.jackett_api_key.clone());
        if let (Some(url), Some(key)) = (jackett_url, jackett_key) {
            let http = http.clone();
            let meta = meta.clone();
            let mt = media_type.to_string();
            let max_process = cfg.jackett_immediate_max_process;
            let max_process_time =
                std::time::Duration::from_secs(cfg.jackett_immediate_max_process_time);
            let query_timeout = std::time::Duration::from_secs(cfg.jackett_search_query_timeout);
            set.spawn(async move {
                let start = Utc::now();
                let t = std::time::Instant::now();
                let streams = jackett::scrape(
                    &http,
                    &url,
                    &key,
                    &meta,
                    &mt,
                    season,
                    episode,
                    max_process,
                    max_process_time,
                    query_timeout,
                )
                .await;
                ("jackett", streams, start, t.elapsed().as_secs_f64())
            });
            spawned_scrapers.push("jackett");
        }
    }

    // ── Zilean ─────────────────────────────────────────────────────────────────
    if cfg.is_scrap_from_zilean && is_stale("zilean", cfg.zilean_search_ttl) {
        let url = cfg.zilean_url.clone();
        let http = http.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        set.spawn(async move {
            let start = Utc::now();
            let t = std::time::Instant::now();
            let streams = zilean::scrape(&http, &url, &meta, &mt, season, episode).await;
            ("zilean", streams, start, t.elapsed().as_secs_f64())
        });
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
            set.spawn(async move {
                let start = Utc::now();
                let t = std::time::Instant::now();
                let streams =
                    torznab::scrape(&http, &torznab_eps, &meta, &mt, season, episode).await;
                ("torznab", streams, start, t.elapsed().as_secs_f64())
            });
            spawned_scrapers.push("torznab");
        }
    }

    // ── User-configured Newznab indexers ─────────────────────────────────────
    // Newznab shares the prowlarr TTL (same cadence as Prowlarr per scraping.rs).
    if is_stale("newznab", cfg.prowlarr_search_ttl) {
        let newznab_idxs: Vec<_> = ic
            .newznab_indexers
            .into_iter()
            .filter(|n| n.enabled)
            .collect();
        if !newznab_idxs.is_empty() {
            let http = http.clone();
            let meta = meta.clone();
            let mt = media_type.to_string();
            set.spawn(async move {
                let start = Utc::now();
                let t = std::time::Instant::now();
                let streams =
                    newznab::scrape(&http, &newznab_idxs, &meta, &mt, season, episode).await;
                ("newznab", streams, start, t.elapsed().as_secs_f64())
            });
            spawned_scrapers.push("newznab");
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
        set.spawn(async move {
            let start = Utc::now();
            let t = std::time::Instant::now();
            let streams = torbox_search::scrape(&http, &ud, &meta, &mt, season, episode).await;
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
        // call public_indexers::scrape directly with the full byparr URL.
        let byparr: Option<String> = None;
        let sites = cfg.public_indexers_live_search_sites.clone();
        let hg = health_gate.clone();
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
    for scraper_id in &spawned_scrapers {
        let _: Result<i64, _> = redis
            .zadd(
                *scraper_id,
                None,
                None,
                false,
                false,
                (now, cache_key.as_str()),
            )
            .await;
    }

    all
}
