/// Shared IPTV import logic (M3U playlists and Xtream Codes).
///
/// Python parity: `m3u_import._import_*_entry`, `_resolve_entry_matched_media_id`,
/// and `import_tasks._process_m3u_import` / `_process_xtream_import`.
use fred::prelude::*;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sqlx::PgPool;
use uuid::Uuid;

use crate::{
    db::IptvSourceType, parser, scrapers::media_resolve::ImportMediaOverrides, state::AppState,
};

use super::{
    import_helpers,
    m3u_import::{M3uEntry, import_tv_channel},
};

// ─── Types ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Default, Serialize)]
pub struct IptvImportStats {
    pub tv: usize,
    pub movie: usize,
    pub series: usize,
    pub failed: usize,
    pub skipped: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct M3uImportOverride {
    pub index: usize,
    #[serde(rename = "type")]
    pub entry_type: String,
    pub media_id: Option<String>,
}

pub struct IptvImportCtx<'a> {
    pub pool: &'a PgPool,
    pub http: &'a Client,
    pub tmdb_api_key: Option<&'a str>,
    pub tvdb_api_key: Option<&'a str>,
    pub cinemeta_enabled: bool,
    pub poster_nsfw_enabled: bool,
}

impl<'a> IptvImportCtx<'a> {
    pub fn from_state(state: &'a AppState) -> Self {
        Self {
            pool: &state.pool,
            http: &state.http,
            tmdb_api_key: state.config.tmdb_api_key.as_deref(),
            tvdb_api_key: state.config.tvdb_api_key.as_deref(),
            cinemeta_enabled: state.config.imdb_cinemeta_fallback_enabled,
            poster_nsfw_enabled: state.config.poster_nsfw_enabled,
        }
    }
}

// ─── Title / type helpers ─────────────────────────────────────────────────────

/// Parsed display fields for an M3U line (Python `parse_title_info`).
pub fn parse_iptv_title_info(name: &str) -> (String, Option<i32>, Option<i32>, Option<i32>) {
    let parsed = parser::parse_title(name);
    let clean = parsed
        .title
        .clone()
        .unwrap_or_else(|| name.trim().to_string());
    let year = parsed.year;
    let season = parsed.seasons.first().copied();
    let episode = parsed.episodes.first().copied();
    (clean, year, season, episode)
}

/// Refine movie → series when SxxExx is present in the title.
pub fn refine_entry_type(entry_type: &str, season: Option<i32>, episode: Option<i32>) -> &str {
    if entry_type == "movie" && (season.is_some() || episode.is_some()) {
        "series"
    } else {
        entry_type
    }
}

pub fn m3u_entry_to_import(entry: &M3uEntry, index: usize) -> M3uEntry {
    let (parsed_title, parsed_year, season, episode) = parse_iptv_title_info(&entry.name);
    let entry_type = refine_entry_type(&entry.entry_type, season, episode).to_string();
    M3uEntry {
        name: entry.name.clone(),
        url: entry.url.clone(),
        logo: entry.logo.clone(),
        group: entry.group.clone(),
        tvg_id: entry.tvg_id.clone(),
        entry_type,
        behavior_hints: entry.behavior_hints.clone(),
        index: Some(index),
        parsed_title: Some(parsed_title),
        parsed_year,
        season,
        episode,
        matched_media_id: None,
    }
}

// ─── Metadata resolution ──────────────────────────────────────────────────────

/// Python `_resolve_entry_matched_media_id`.
pub async fn resolve_entry_matched_media_id(
    ctx: &IptvImportCtx<'_>,
    entry: &M3uEntry,
    media_type: &str,
) -> Option<String> {
    if let Some(ref id) = entry.matched_media_id
        && !id.is_empty() {
            return Some(id.clone());
        }

    let title = entry.parsed_title.as_deref().unwrap_or(&entry.name);
    let matches = crate::scrapers::metadata::search_import_matches(
        ctx.http,
        ctx.pool,
        title,
        entry.parsed_year,
        media_type,
        ctx.tmdb_api_key,
        ctx.tvdb_api_key,
        ctx.cinemeta_enabled,
    )
    .await;

    matches
        .first()
        .and_then(|m| m.get("id").and_then(|v| v.as_str()).map(str::to_string))
}

async fn resolve_media_for_entry(
    ctx: &IptvImportCtx<'_>,
    entry: &M3uEntry,
    media_type: &str,
    user_id: i64,
    is_public: bool,
) -> Option<i32> {
    let title = entry.parsed_title.as_deref().unwrap_or(&entry.name);

    let meta_id = if let Some(ref ext) = entry.matched_media_id {
        if !ext.is_empty() {
            ext.clone()
        } else {
            format!("mf:user:{user_id}:{}", &Uuid::new_v4().to_string()[..8])
        }
    } else {
        format!("mf:user:{user_id}:{}", &Uuid::new_v4().to_string()[..8])
    };

    let media_id = import_helpers::resolve_media_for_import(
        ctx.pool,
        ctx.http,
        ctx.tmdb_api_key,
        ctx.tvdb_api_key,
        &meta_id,
        media_type,
        ImportMediaOverrides {
            title: Some(title),
            poster: entry.logo.as_deref(),
            background: None,
            release_date: None,
            year: entry.parsed_year,
        },
        None,
        ctx.poster_nsfw_enabled,
    )
    .await?;

    if meta_id.starts_with("mf:user:") {
        let _ = sqlx::query(
            "UPDATE media SET created_by_user_id = $1, is_user_created = true, is_public = $2, updated_at = NOW() WHERE id = $3",
        )
        .bind(user_id as i32)
        .bind(is_public)
        .bind(media_id)
        .execute(ctx.pool)
        .await;
    }

    Some(media_id)
}

// ─── HTTP stream insert (shared with Xtream job layer) ────────────────────────

/// Insert HTTP stream linked to media. Returns `true` when a new stream was created.
#[allow(clippy::too_many_arguments)]
pub async fn insert_http_stream_for_media(
    pool: &PgPool,
    media_id: i32,
    stream_name: &str,
    url: &str,
    source_label: &str,
    is_public: bool,
    behavior_hints: Option<&serde_json::Value>,
    uploader_user_id: Option<i64>,
) -> Result<bool, sqlx::Error> {
    let existing: Option<i32> = sqlx::query_scalar(
        "SELECT hs.stream_id FROM http_stream hs
         JOIN stream_media_link sml ON sml.stream_id = hs.stream_id
         WHERE hs.url = $1 AND sml.media_id = $2 LIMIT 1",
    )
    .bind(url)
    .bind(media_id)
    .fetch_optional(pool)
    .await?;

    if existing.is_some() {
        return Ok(false);
    }

    let normalized = crate::db::HttpStoreInput {
        base: crate::db::StreamStoreBase {
            name: stream_name.to_string(),
            source: source_label.to_string(),
            uploader_user_id: uploader_user_id.map(|id| id as i32),
            is_public,
            ..Default::default()
        },
        url: url.to_string(),
        format: None,
        behavior_hints: behavior_hints.cloned(),
        drm_key_id: None,
        drm_key: None,
        extractor_name: None,
    };

    let opts = crate::db::StoreStreamOpts::user_import(
        crate::db::MediaId(media_id),
        crate::db::MediaType::Movie,
    );

    Ok(crate::db::store_http_stream(pool, &normalized, &opts)
        .await?
        .was_inserted())
}

// ─── Per-entry import (Python `_import_*_entry`) ─────────────────────────────

pub struct TvImportResult {
    pub stream_created: bool,
    pub stream_existed: bool,
}

pub async fn import_tv_entry(
    pool: &PgPool,
    entry: &M3uEntry,
    source: &str,
    _user_id: i64,
    _is_public: bool,
) -> TvImportResult {
    let created = import_tv_channel(
        pool,
        &entry.name,
        &entry.url,
        entry.logo.as_deref(),
        entry.group.as_deref(),
        source,
        entry.behavior_hints.as_ref(),
    )
    .await;
    TvImportResult {
        stream_created: created,
        stream_existed: !created,
    }
}

pub async fn import_movie_entry(
    ctx: &IptvImportCtx<'_>,
    entry: &M3uEntry,
    source: &str,
    user_id: i64,
    is_public: bool,
) -> Result<bool, sqlx::Error> {
    let media_id = match resolve_media_for_entry(ctx, entry, "movie", user_id, is_public).await {
        Some(id) => id,
        None => return Ok(false),
    };

    insert_http_stream_for_media(
        ctx.pool,
        media_id,
        &entry.name,
        &entry.url,
        source,
        is_public,
        entry.behavior_hints.as_ref(),
        Some(user_id),
    )
    .await
}

pub async fn import_series_entry(
    ctx: &IptvImportCtx<'_>,
    entry: &M3uEntry,
    source: &str,
    user_id: i64,
    is_public: bool,
) -> Result<bool, sqlx::Error> {
    let media_id = match resolve_media_for_entry(ctx, entry, "series", user_id, is_public).await {
        Some(id) => id,
        None => return Ok(false),
    };

    let season = entry.season.unwrap_or(1);
    let episode = entry.episode.unwrap_or(1);

    let existing: Option<i32> = sqlx::query_scalar(
        "SELECT hs.stream_id FROM http_stream hs
         JOIN stream_file sf ON sf.stream_id = hs.stream_id
         JOIN file_media_link fml ON fml.file_id = sf.id
         WHERE hs.url = $1 AND fml.media_id = $2 AND fml.season_number = $3 AND fml.episode_number = $4
         LIMIT 1",
    )
    .bind(&entry.url)
    .bind(media_id)
    .bind(season)
    .bind(episode)
    .fetch_optional(ctx.pool)
    .await?;

    if existing.is_some() {
        return Ok(false);
    }

    let normalized = crate::db::HttpStoreInput {
        base: crate::db::StreamStoreBase {
            name: entry.name.clone(),
            source: source.to_string(),
            uploader_user_id: Some(user_id as i32),
            is_public,
            ..Default::default()
        },
        url: entry.url.clone(),
        format: None,
        behavior_hints: entry.behavior_hints.clone(),
        drm_key_id: None,
        drm_key: None,
        extractor_name: None,
    };

    let opts = crate::db::StoreStreamOpts::user_import(
        crate::db::MediaId(media_id),
        crate::db::MediaType::Series,
    )
    .with_episode(Some(season), Some(episode), None);

    Ok(crate::db::store_http_stream(ctx.pool, &normalized, &opts)
        .await?
        .was_inserted())
}

/// Process one M3U row (respecting optional user override).
pub async fn process_m3u_entry(
    ctx: &IptvImportCtx<'_>,
    entry: &mut M3uEntry,
    source: &str,
    user_id: i64,
    is_public: bool,
    override_map: &std::collections::HashMap<usize, M3uImportOverride>,
) {
    let idx = entry.index.unwrap_or(0);
    if let Some(ov) = override_map.get(&idx) {
        if !ov.entry_type.is_empty() {
            entry.entry_type = ov.entry_type.clone();
        }
        if let Some(ref mid) = ov.media_id
            && !mid.is_empty() {
                entry.matched_media_id = Some(mid.clone());
            }
    }

    match entry.entry_type.as_str() {
        "tv" => {
            let r = import_tv_entry(ctx.pool, entry, source, user_id, is_public).await;
            // stats updated by caller
            let _ = r;
        }
        "movie" => {
            if entry.matched_media_id.is_none()
                && let Some(id) = resolve_entry_matched_media_id(ctx, entry, "movie").await {
                    entry.matched_media_id = Some(id);
                }
            let _ = import_movie_entry(ctx, entry, source, user_id, is_public).await;
        }
        "series" => {
            if entry.matched_media_id.is_none()
                && let Some(id) = resolve_entry_matched_media_id(ctx, entry, "series").await {
                    entry.matched_media_id = Some(id);
                }
            let _ = import_series_entry(ctx, entry, source, user_id, is_public).await;
        }
        _ => {}
    }
}

/// Run a full M3U batch and return stats.
pub async fn run_m3u_import_batch(
    ctx: &IptvImportCtx<'_>,
    entries: Vec<M3uEntry>,
    source: &str,
    user_id: i64,
    is_public: bool,
    override_map: std::collections::HashMap<usize, M3uImportOverride>,
) -> IptvImportStats {
    let mut stats = IptvImportStats::default();

    for (i, mut entry) in entries.into_iter().enumerate() {
        if entry.index.is_none() {
            entry.index = Some(i);
        }
        if let Some(ov) = override_map.get(&entry.index.unwrap_or(i)) {
            if !ov.entry_type.is_empty() {
                entry.entry_type = ov.entry_type.clone();
            }
            if let Some(ref mid) = ov.media_id
                && !mid.is_empty() {
                    entry.matched_media_id = Some(mid.clone());
                }
        }
        let entry_type = entry.entry_type.clone();

        match entry_type.as_str() {
            "tv" => {
                let r = import_tv_entry(ctx.pool, &entry, source, user_id, is_public).await;
                if r.stream_created {
                    stats.tv += 1;
                } else if r.stream_existed {
                    stats.skipped += 1;
                }
            }
            "movie" => {
                if entry.matched_media_id.is_none()
                    && let Some(id) = resolve_entry_matched_media_id(ctx, &entry, "movie").await {
                        entry.matched_media_id = Some(id);
                    }
                match import_movie_entry(ctx, &entry, source, user_id, is_public).await {
                    Ok(true) => stats.movie += 1,
                    Ok(false) => stats.skipped += 1,
                    Err(e) => {
                        tracing::warn!("m3u movie import failed for {}: {e}", entry.name);
                        stats.failed += 1;
                    }
                }
            }
            "series" => {
                if entry.matched_media_id.is_none()
                    && let Some(id) = resolve_entry_matched_media_id(ctx, &entry, "series").await {
                        entry.matched_media_id = Some(id);
                    }
                match import_series_entry(ctx, &entry, source, user_id, is_public).await {
                    Ok(true) => stats.series += 1,
                    Ok(false) => stats.skipped += 1,
                    Err(e) => {
                        tracing::warn!("m3u series import failed for {}: {e}", entry.name);
                        stats.failed += 1;
                    }
                }
            }
            _ => stats.skipped += 1,
        }
    }

    stats
}

// ─── Redis job helpers ────────────────────────────────────────────────────────

pub async fn update_import_job(
    redis: &fred::clients::Client,
    job_key: &str,
    status: &str,
    progress: usize,
    total: usize,
    stats: &IptvImportStats,
) {
    update_import_job_full(
        redis, job_key, status, progress, total, stats, None, None, None,
    )
    .await;
}

pub async fn update_import_job_full(
    redis: &fred::clients::Client,
    job_key: &str,
    status: &str,
    progress: usize,
    total: usize,
    stats: &IptvImportStats,
    user_id: Option<i64>,
    source_type: Option<&str>,
    error: Option<&str>,
) {
    let existing: Option<String> = redis.get(job_key).await.unwrap_or(None);
    let mut body = existing
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .unwrap_or_else(|| json!({}));
    if body.get("job_id").is_none()
        && let Some(id) = job_key.strip_prefix("import_job:") {
            body["job_id"] = json!(id);
        }
    body["status"] = json!(status);
    body["progress"] = json!(progress);
    body["total"] = json!(total);
    body["stats"] = json!(stats);
    if let Some(uid) = user_id {
        body["user_id"] = json!(uid);
    }
    if let Some(st) = source_type {
        body["source_type"] = json!(st);
    }
    if let Some(err) = error {
        body["error"] = json!(err);
    }
    body["updated_at"] = json!(chrono::Utc::now().to_rfc3339());
    let _ = redis
        .set::<(), _, _>(
            job_key,
            body.to_string(),
            Some(Expiration::EX(86400)),
            None,
            false,
        )
        .await;
}

/// Xtream live/VOD/series import from cached analyze payload (route handler parity).
#[allow(clippy::too_many_arguments)]
pub async fn run_xtream_import_batch(
    ctx: &IptvImportCtx<'_>,
    server_url: &str,
    username: &str,
    password: &str,
    source_name: &str,
    user_id: i64,
    is_public: bool,
    import_live: bool,
    import_vod: bool,
    import_series: bool,
    live_streams: &[serde_json::Value],
    vod_streams: &[serde_json::Value],
    series_list: &[serde_json::Value],
) -> IptvImportStats {
    let mut stats = IptvImportStats::default();
    let server = server_url.trim_end_matches('/');

    if import_live {
        for stream in live_streams {
            let name = stream
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown");
            let stream_id = stream
                .get("stream_id")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            let logo = stream
                .get("stream_icon")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty());
            let url = format!("{server}/live/{username}/{password}/{stream_id}.m3u8");
            if import_tv_channel(ctx.pool, name, &url, logo, None, source_name, None).await {
                stats.tv += 1;
            } else {
                stats.skipped += 1;
            }
        }
    }

    if import_vod {
        for stream in vod_streams {
            let name = stream
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown");
            let stream_id = stream
                .get("stream_id")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            let ext = stream
                .get("container_extension")
                .and_then(|v| v.as_str())
                .unwrap_or("mp4");
            let logo = stream
                .get("stream_icon")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty());
            let url = format!("{server}/movie/{username}/{password}/{stream_id}.{ext}");
            let year = stream
                .get("year")
                .and_then(|v| v.as_str())
                .and_then(|s| s.parse::<i32>().ok())
                .or_else(|| {
                    stream
                        .get("year")
                        .and_then(|v| v.as_i64())
                        .map(|n| n as i32)
                });

            let mut entry = M3uEntry {
                name: name.to_string(),
                url,
                logo: logo.map(str::to_string),
                group: None,
                tvg_id: None,
                entry_type: "movie".to_string(),
                behavior_hints: None,
                index: None,
                parsed_title: Some(name.to_string()),
                parsed_year: year,
                season: None,
                episode: None,
                matched_media_id: None,
            };

            if let Some(id) = resolve_entry_matched_media_id(ctx, &entry, "movie").await {
                entry.matched_media_id = Some(id);
            }

            match import_movie_entry(ctx, &entry, source_name, user_id, is_public).await {
                Ok(true) => stats.movie += 1,
                Ok(false) => stats.skipped += 1,
                Err(e) => {
                    tracing::warn!("xtream VOD import failed for {name}: {e}");
                    stats.failed += 1;
                }
            }
        }
    }

    if import_series {
        for series in series_list {
            let series_id = series
                .get("series_id")
                .and_then(|v| v.as_i64())
                .or_else(|| {
                    series
                        .get("series_id")
                        .and_then(|v| v.as_str())
                        .and_then(|s| s.parse().ok())
                });
            let Some(series_id) = series_id else {
                continue;
            };

            let series_name = series
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown");
            let cover = series
                .get("cover")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty());

            let info_url = format!(
                "{server}/player_api.php?username={}&password={}&action=get_series_info&series_id={series_id}",
                urlencoding::encode(username),
                urlencoding::encode(password),
            );

            let info: serde_json::Value = match ctx.http.get(&info_url).send().await {
                Ok(r) if r.status().is_success() => r.json().await.unwrap_or_default(),
                _ => continue,
            };

            let episodes_obj = info.get("episodes").cloned().unwrap_or(json!({}));
            let Some(seasons) = episodes_obj.as_object() else {
                continue;
            };

            for (season_num, eps) in seasons {
                let season: i32 = season_num.parse().unwrap_or(1);
                let Some(ep_list) = eps.as_array() else {
                    continue;
                };
                for ep in ep_list {
                    let ep_id = ep
                        .get("id")
                        .and_then(|v| v.as_str().map(str::to_string))
                        .or_else(|| ep.get("id").and_then(|v| v.as_i64()).map(|n| n.to_string()));
                    let Some(ep_id) = ep_id else {
                        continue;
                    };
                    let ep_num = ep.get("episode_num").and_then(|v| v.as_i64()).unwrap_or(1) as i32;
                    let ep_ext = ep
                        .get("container_extension")
                        .and_then(|v| v.as_str())
                        .unwrap_or("mp4");
                    let ep_title = ep
                        .get("title")
                        .and_then(|v| v.as_str())
                        .unwrap_or(series_name);
                    let url = format!("{server}/series/{username}/{password}/{ep_id}.{ep_ext}");

                    let mut entry = M3uEntry {
                        name: if ep_title != series_name {
                            ep_title.to_string()
                        } else {
                            format!("{series_name} S{season:02}E{ep_num:02}")
                        },
                        url,
                        logo: cover.map(str::to_string),
                        group: None,
                        tvg_id: None,
                        entry_type: "series".to_string(),
                        behavior_hints: None,
                        index: None,
                        parsed_title: Some(series_name.to_string()),
                        parsed_year: None,
                        season: Some(season),
                        episode: Some(ep_num),
                        matched_media_id: None,
                    };

                    if let Some(id) = resolve_entry_matched_media_id(ctx, &entry, "series").await {
                        entry.matched_media_id = Some(id);
                    }

                    match import_series_entry(ctx, &entry, source_name, user_id, is_public).await {
                        Ok(true) => stats.series += 1,
                        Ok(false) => stats.skipped += 1,
                        Err(e) => {
                            tracing::warn!("xtream series import failed: {e}");
                            stats.failed += 1;
                        }
                    }
                }
            }
        }
    }

    stats
}

/// Save M3U playlist URL as a re-syncable IPTV source (Python `import_m3u_playlist` save_source).
/// Save Xtream credentials for re-sync (Python `import_xtream` save_source).
pub async fn save_xtream_iptv_source(
    pool: &PgPool,
    user_id: i64,
    name: &str,
    server_url: &str,
    encrypted_credentials: &str,
    is_public: bool,
    import_live: bool,
    import_vod: bool,
    import_series: bool,
    live_category_ids: Option<&[String]>,
    vod_category_ids: Option<&[String]>,
    series_category_ids: Option<&[String]>,
    stats: &IptvImportStats,
) -> Result<i32, sqlx::Error> {
    let stats_json = serde_json::to_value(stats).unwrap_or(serde_json::json!({}));
    let live_ids = live_category_ids
        .map(serde_json::to_value)
        .transpose()
        .ok()
        .flatten();
    let vod_ids = vod_category_ids
        .map(serde_json::to_value)
        .transpose()
        .ok()
        .flatten();
    let series_ids = series_category_ids
        .map(serde_json::to_value)
        .transpose()
        .ok()
        .flatten();
    sqlx::query_scalar(
        r#"INSERT INTO iptv_source (
               user_id, source_type, name, server_url, encrypted_credentials,
               is_public, import_live, import_vod, import_series,
               live_category_ids, vod_category_ids, series_category_ids,
               last_synced_at, last_sync_stats, is_active, created_at, updated_at
           ) VALUES (
               $1, $2, $3, $4, $5,
               $6, $7, $8, $9,
               $10::jsonb, $11::jsonb, $12::jsonb,
               NOW(), $13::jsonb, true, NOW(), NOW()
           ) RETURNING id"#,
    )
    .bind(user_id as i32)
    .bind(IptvSourceType::Xtream)
    .bind(name)
    .bind(server_url)
    .bind(encrypted_credentials)
    .bind(is_public)
    .bind(import_live)
    .bind(import_vod)
    .bind(import_series)
    .bind(live_ids)
    .bind(vod_ids)
    .bind(series_ids)
    .bind(stats_json)
    .fetch_one(pool)
    .await
}

pub async fn save_m3u_iptv_source(
    pool: &PgPool,
    user_id: i64,
    name: &str,
    m3u_url: &str,
    is_public: bool,
    stats: &IptvImportStats,
) -> Result<i32, sqlx::Error> {
    let stats_json = serde_json::to_value(stats).unwrap_or(serde_json::json!({}));
    sqlx::query_scalar(
        r#"INSERT INTO iptv_source (
               user_id, source_type, name, m3u_url, is_public,
               import_live, import_vod, import_series,
               last_synced_at, last_sync_stats, is_active, created_at, updated_at
           ) VALUES (
               $1, $2, $3, $4, $5,
               true, true, true,
               NOW(), $6::jsonb, true, NOW(), NOW()
           ) RETURNING id"#,
    )
    .bind(user_id as i32)
    .bind(IptvSourceType::M3u)
    .bind(name)
    .bind(m3u_url)
    .bind(is_public)
    .bind(stats_json)
    .fetch_one(pool)
    .await
}

pub fn parse_override_map(
    overrides_json: Option<&str>,
) -> std::collections::HashMap<usize, M3uImportOverride> {
    let mut map = std::collections::HashMap::new();
    let Some(raw) = overrides_json else {
        return map;
    };
    if raw.trim().is_empty() {
        return map;
    }
    if let Ok(list) = serde_json::from_str::<Vec<M3uImportOverride>>(raw) {
        for ov in list {
            map.insert(ov.index, ov);
        }
    }
    map
}
