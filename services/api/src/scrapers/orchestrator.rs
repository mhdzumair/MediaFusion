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
/// - `live_search_streams` is disabled in config
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
    if !state.config.live_search_streams {
        return vec![];
    }

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

    let results = fan_out(state, user_data, meta, media_type, season, episode).await;

    // Deduplicate by info_hash (first occurrence wins).
    let mut seen = std::collections::HashSet::new();
    let deduped: Vec<ScrapedStream> = results
        .into_iter()
        .filter(|s| seen.insert(s.info_hash.clone()))
        .collect();

    // Persist to DB + write Redis blob for future warm-path hits.
    persist::write_back(
        &deduped,
        &state.pool,
        &state.redis,
        meta,
        media_type,
        season,
        episode,
        scope,
    )
    .await;

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
    if let (Some(u), Some(p)) = (
        state.config.easynews_username.as_deref(),
        state.config.easynews_password.as_deref(),
    ) {
        if !u.is_empty() && !p.is_empty() {
            let en = easynews::scrape_with_credentials(
                &state.http, u, p, meta, media_type, season, episode,
            )
            .await;
            results.extend(en);
        }
    }

    // ── TorBox Usenet ─────────────────────────────────────────────────────────
    let tb = torbox_search::scrape_usenet(
        &state.http, user_data, meta, media_type, season, episode,
    )
    .await;
    results.extend(tb);

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
) -> Vec<ScrapedStream> {
    let http = state.http.clone();
    let cfg = state.config.clone();
    let meta = Arc::new(meta.clone());
    let ic = user_data
        .indexer_config
        .clone()
        .unwrap_or_default();

    let mut set: JoinSet<Vec<ScrapedStream>> = JoinSet::new();

    // ── Torrentio ──────────────────────────────────────────────────────────────
    {
        let http = http.clone();
        let url = cfg.torrentio_url.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        set.spawn(async move {
            torrentio::scrape(&http, &url, &meta, &mt, season, episode).await
        });
    }

    // ── Prowlarr (global config, or user-overridden URL/key) ──────────────────
    {
        let prowlarr_url = ic
            .prowlarr
            .as_ref()
            .and_then(|p| if p.use_global { None } else { p.url.clone() })
            .or_else(|| cfg.prowlarr_url.clone());
        let prowlarr_key = ic
            .prowlarr
            .as_ref()
            .and_then(|p| if p.use_global { None } else { p.api_key.clone() })
            .or_else(|| cfg.prowlarr_api_key.clone());
        if let (Some(url), Some(key)) = (prowlarr_url, prowlarr_key) {
            let http = http.clone();
            let meta = meta.clone();
            let mt = media_type.to_string();
            set.spawn(async move {
                prowlarr::scrape(&http, &url, &key, &meta, &mt, season, episode).await
            });
        }
    }

    // ── Jackett (global config, or user-overridden) ───────────────────────────
    {
        let jackett_url = ic
            .jackett
            .as_ref()
            .and_then(|j| if j.use_global { None } else { j.url.clone() })
            .or_else(|| cfg.jackett_url.clone());
        let jackett_key = ic
            .jackett
            .as_ref()
            .and_then(|j| if j.use_global { None } else { j.api_key.clone() })
            .or_else(|| cfg.jackett_api_key.clone());
        if let (Some(url), Some(key)) = (jackett_url, jackett_key) {
            let http = http.clone();
            let meta = meta.clone();
            let mt = media_type.to_string();
            set.spawn(async move {
                jackett::scrape(&http, &url, &key, &meta, &mt, season, episode).await
            });
        }
    }

    // ── Zilean ─────────────────────────────────────────────────────────────────
    if let Some(url) = cfg.zilean_url.clone() {
        let http = http.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        set.spawn(async move {
            zilean::scrape(&http, &url, &meta, &mt, season, episode).await
        });
    }

    // ── User-configured Torznab endpoints ─────────────────────────────────────
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
            torznab::scrape(&http, &torznab_eps, &meta, &mt, season, episode).await
        });
    }

    // ── User-configured Newznab indexers ──────────────────────────────────────
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
            newznab::scrape(&http, &newznab_idxs, &meta, &mt, season, episode).await
        });
    }

    // ── MediaFusion peer ──────────────────────────────────────────────────────
    if let Some(url) = cfg.mediafusion_url.clone() {
        let http = http.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        set.spawn(async move {
            mediafusion::scrape(&http, &url, &meta, &mt, season, episode).await
        });
    }

    // ── TorBox Search (user token) ────────────────────────────────────────────
    {
        let http = http.clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        let ud = user_data.clone();
        set.spawn(async move {
            torbox_search::scrape(&http, &ud, &meta, &mt, season, episode).await
        });
    }

    // ── Public indexers (stub) ────────────────────────────────────────────────
    let _ = public_indexers::scrape;
    // ── Public usenet (stub) ─────────────────────────────────────────────────
    let _ = public_usenet::scrape;

    let mut all: Vec<ScrapedStream> = Vec::new();
    while let Some(res) = set.join_next().await {
        match res {
            Ok(streams) => all.extend(streams),
            Err(e) => tracing::warn!("orchestrator: scraper task panicked: {e}"),
        }
    }
    all
}
