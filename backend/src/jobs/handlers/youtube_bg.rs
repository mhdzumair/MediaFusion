use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use tracing::{debug, info, warn};

use fred::prelude::*;

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    routes::content::import_helpers,
};

pub struct YoutubeBgScraper;

const REDIS_KEY: &str = "background_search:youtube";
/// 48 hours in seconds.
const RESCRAPE_AFTER_SECS: u64 = 48 * 60 * 60;
/// How many items to process per job run.
const BATCH_SIZE: usize = 5;

#[derive(Debug, Deserialize, Serialize)]
struct YtCandidate {
    title: String,
    year: Option<i32>,
    media_id: i32,
    media_type: String,
    #[serde(default)]
    last_scrape: u64,
}

/// A single result line from `yt-dlp --dump-json --flat-playlist`.
#[derive(Debug, Deserialize)]
struct YtDlpEntry {
    id: String,
    title: Option<String>,
    duration: Option<f64>,
    webpage_url: Option<String>,
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

/// Run yt-dlp and return parsed entries.
async fn run_yt_dlp(query: &str) -> Result<Vec<YtDlpEntry>, JobError> {
    let output = tokio::process::Command::new("yt-dlp")
        .args([
            "--dump-json",
            "--flat-playlist",
            "--no-playlist",
            "--match-filter",
            "duration > 1200", // > 20 min
            "--max-downloads",
            "3",
            query,
        ])
        .output()
        .await
        .map_err(|e| JobError::other(format!("yt-dlp exec error: {e}")))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        debug!("youtube_bg: yt-dlp stderr: {stderr}");
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let entries: Vec<YtDlpEntry> = stdout
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            if line.is_empty() {
                return None;
            }
            match serde_json::from_str::<YtDlpEntry>(line) {
                Ok(e) => Some(e),
                Err(err) => {
                    debug!("youtube_bg: failed to parse yt-dlp line: {err}");
                    None
                }
            }
        })
        .collect();

    Ok(entries)
}

#[async_trait]
impl JobHandler for YoutubeBgScraper {
    const QUEUE: &'static str = "youtube_bg";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        // ── 1. Read pending YouTube candidates from Redis ─────────────────────
        let raw_map: HashMap<String, String> = ctx
            .state
            .redis
            .hgetall::<HashMap<String, String>, _>(REDIS_KEY)
            .await
            .unwrap_or_default();

        if raw_map.is_empty() {
            info!("youtube_bg: no pending candidates in {REDIS_KEY}");
            return Ok(());
        }

        let now = now_secs();

        // Parse and filter to items due for (re-)scrape.
        let mut due: Vec<(String, YtCandidate)> = raw_map
            .into_iter()
            .filter_map(|(key, val)| {
                let candidate: YtCandidate = serde_json::from_str(&val).ok()?;
                let age = now.saturating_sub(candidate.last_scrape);
                if candidate.last_scrape == 0 || age >= RESCRAPE_AFTER_SECS {
                    Some((key, candidate))
                } else {
                    None
                }
            })
            .collect();

        // Take up to BATCH_SIZE items.
        due.truncate(BATCH_SIZE);

        if due.is_empty() {
            info!("youtube_bg: all candidates scraped recently, nothing to do");
            return Ok(());
        }

        info!("youtube_bg: processing {} candidates", due.len());

        // ── 2. Process each candidate ─────────────────────────────────────────
        for (key, mut candidate) in due {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let year_str = candidate.year.map(|y| y.to_string()).unwrap_or_default();
            let query = format!("ytsearch5:{} {} official", candidate.title, year_str);

            info!(
                "youtube_bg: searching for '{}' (media_id={})",
                candidate.title, candidate.media_id
            );

            let entries = match run_yt_dlp(&query).await {
                Ok(e) => e,
                Err(e) => {
                    warn!("youtube_bg: yt-dlp failed for '{}': {e}", candidate.title);
                    // Still update last_scrape so we don't hammer a failing query.
                    candidate.last_scrape = now;
                    let updated = serde_json::to_string(&candidate).unwrap_or_default();
                    let _: Result<(), _> = ctx
                        .state
                        .redis
                        .hset::<(), _, _>(REDIS_KEY, (key.clone(), updated))
                        .await;
                    continue;
                }
            };

            if entries.is_empty() {
                info!("youtube_bg: no results for '{}'", candidate.title);
            }

            for entry in &entries {
                let url = entry.webpage_url.as_deref().unwrap_or_default();
                let title = entry.title.as_deref().unwrap_or("(unknown)");
                let duration_secs = entry.duration.unwrap_or(0.0) as u64;

                info!(
                    "youtube_bg: inserting youtube_stream: \
                     youtube_id={}, title={:?}, duration={}s, url={}, \
                     for media_id={}",
                    entry.id, title, duration_secs, url, candidate.media_id
                );

                // ── 1. Skip if video_id already in DB ────────────────────
                let already_exists: Option<i32> = sqlx::query_scalar(
                    "SELECT stream_id FROM youtube_stream WHERE video_id = $1 LIMIT 1",
                )
                .bind(&entry.id)
                .fetch_optional(&ctx.state.pool)
                .await
                .unwrap_or(None);

                if already_exists.is_some() {
                    debug!("youtube_bg: video_id={} already in DB, skipping", entry.id);
                    continue;
                }

                // ── 2. Insert base stream row ─────────────────────────────
                let stream_id: Option<i32> = sqlx::query_scalar(
                    r#"INSERT INTO stream (
                        stream_type, name, source, is_active, is_blocked, is_public,
                        playback_count, is_remastered, is_upscaled, is_proper, is_repack,
                        is_extended, is_complete, is_dubbed, is_subbed, updated_at, created_at
                    ) VALUES (
                        'YOUTUBE'::streamtype, $1, 'youtube_bg', true, false, true, 0,
                        false, false, false, false, false, false, false, false, NOW(), NOW()
                    ) RETURNING id"#,
                )
                .bind(title)
                .fetch_optional(&ctx.state.pool)
                .await
                .unwrap_or(None);

                let stream_id = match stream_id {
                    Some(id) => id,
                    None => {
                        warn!(
                            "youtube_bg: failed to insert stream for youtube_id={}",
                            entry.id
                        );
                        continue;
                    }
                };

                // ── 3. Insert youtube_stream row ──────────────────────────
                let duration_i32 = if duration_secs > i32::MAX as u64 {
                    None
                } else {
                    Some(duration_secs as i32)
                };

                if let Err(e) = sqlx::query(
                    "INSERT INTO youtube_stream (stream_id, video_id, duration_seconds, is_live, is_premiere) \
                     VALUES ($1, $2, $3, false, false) ON CONFLICT (video_id) DO NOTHING",
                )
                .bind(stream_id)
                .bind(&entry.id)
                .bind(duration_i32)
                .execute(&ctx.state.pool)
                .await
                {
                    warn!("youtube_bg: youtube_stream insert error for {}: {e}", entry.id);
                    // Clean up the orphaned stream row
                    let _ = sqlx::query("DELETE FROM stream WHERE id = $1")
                        .bind(stream_id)
                        .execute(&ctx.state.pool)
                        .await;
                    continue;
                }

                let _ = import_helpers::link_stream_to_media(
                    &ctx.state.pool,
                    stream_id,
                    candidate.media_id,
                )
                .await;
            }

            // ── 3. Mark as scraped ────────────────────────────────────────────
            candidate.last_scrape = now;
            let updated = serde_json::to_string(&candidate).map_err(JobError::Serde)?;
            let _: Result<(), _> = ctx
                .state
                .redis
                .hset::<(), _, _>(REDIS_KEY, (key.clone(), updated))
                .await;

            debug!("youtube_bg: updated last_scrape for key '{key}'");
        }

        info!("youtube_bg: done");
        Ok(())
    }
}
