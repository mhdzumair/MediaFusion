use std::sync::Arc;

use fred::prelude::*;
use tokio::task::JoinSet;

use crate::{
    models::user_data::UserData,
    scrapers::{
        easynews, jackett, mediafusion, newznab, persist, prowlarr, public_indexers, public_usenet,
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
    scope: &str,
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

    // Persist to DB, then invalidate the stream cache so the next request
    // cold-paths through DB and gets a complete, fully-populated blob.
    persist::write_back(&deduped, &state.pool, meta, media_type, season, episode).await;
    invalidate_stream_cache(&state.redis, meta, media_type, season, episode, scope).await;

    // ── Telegram live scraper (Phase 2c) ─────────────────────────────────────
    if let Some(ref tg_client) = state.telegram {
        let tg_results = telegram::scrape(
            tg_client,
            &state.config.telegram_scraping_channels,
            &[], // user-specific channels (future: pull from user_data)
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

    // Release lock.
    let _: Result<(), _> = state.redis.del(&lock_key).await;

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
    invalidate_stream_cache(&state.redis, meta, media_type, season, episode, scope).await;

    if let Some(ref tg_client) = state.telegram {
        let tg_results = telegram::scrape(
            tg_client,
            &state.config.telegram_scraping_channels,
            &[],
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
        let pu = public_usenet::scrape(&state.http, meta, media_type, season, episode).await;
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

    let mut set: JoinSet<(&'static str, Vec<ScrapedStream>)> = JoinSet::new();
    let mut spawned_scrapers: Vec<&'static str> = Vec::new();

    // ── Torrentio ──────────────────────────────────────────────────────────────
    if cfg.is_scrap_from_torrentio && is_stale("torrentio", cfg.torrentio_search_ttl) {
        let http = http.clone();
        let url = cfg.torrentio_url.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        set.spawn(async move {
            (
                "torrentio",
                torrentio::scrape(&http, &url, &meta, &mt, season, episode).await,
            )
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
                (
                    "prowlarr",
                    prowlarr::scrape(
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
                    .await,
                )
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
                (
                    "jackett",
                    jackett::scrape(
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
                    .await,
                )
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
            (
                "zilean",
                zilean::scrape(&http, &url, &meta, &mt, season, episode).await,
            )
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
                (
                    "torznab",
                    torznab::scrape(&http, &torznab_eps, &meta, &mt, season, episode).await,
                )
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
                (
                    "newznab",
                    newznab::scrape(&http, &newznab_idxs, &meta, &mt, season, episode).await,
                )
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
            (
                "mediafusion",
                mediafusion::scrape(
                    &http,
                    &url,
                    &meta,
                    &mt,
                    season,
                    episode,
                    secret_str.as_deref(),
                )
                .await,
            )
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
            (
                "torbox_search",
                torbox_search::scrape(&http, &ud, &meta, &mt, season, episode).await,
            )
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
        let byparr = cfg.byparr_url.clone();
        let sites = cfg.public_indexers_live_search_sites.clone();
        set.spawn(async move {
            (
                "public_indexers",
                public_indexers::scrape(
                    &http,
                    &meta,
                    &mt,
                    season,
                    episode,
                    byparr.as_deref(),
                    sites.as_deref(),
                )
                .await,
            )
        });
        spawned_scrapers.push("public_indexers");
    }

    let mut all: Vec<ScrapedStream> = Vec::new();
    while let Some(res) = set.join_next().await {
        match res {
            Ok((id, streams)) => {
                tracing::info!("scraper {id}: {} streams for {}", streams.len(), cache_key);
                all.extend(streams);
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
