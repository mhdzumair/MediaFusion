/// Background job: classify poster images with the NSFW ONNX model and update
/// `poster_nsfw_score`, `poster_nsfw_flagged`, `poster_nsfw_model_ver` on `media`.
///
/// The job iterates all media rows whose poster has not yet been classified by the
/// current model version (or where `poster_nsfw_model_ver` is NULL), in batches of
/// `AppConfig::poster_nsfw_scan_batch`, so it never locks the table long and can be
/// interrupted cleanly via the cancellation token.
///
/// Per-batch telemetry is logged at INFO level:
///   classified=N  flagged=N  skipped=N  batch_ms=N  rss_mb=N
///
/// Rows where `poster_nsfw_reviewed = true` are never overwritten.
use std::time::Instant;

use async_trait::async_trait;
use sqlx::Row;
use tracing::{info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    nsfw::read_rss_kb,
};

pub struct PosterNsfwScan;

#[async_trait]
impl JobHandler for PosterNsfwScan {
    const QUEUE: &'static str = "poster_nsfw_scan";
    const CONCURRENCY: usize = 1;
    const MAX_ATTEMPTS: i32 = 3;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let state = &ctx.state;
        let cfg = &state.config;

        // Optional arg: {"media_id": N} — classify a single row (enqueued at import time).
        let single_media_id = args
            .get("media_id")
            .and_then(|v| v.as_i64())
            .map(|v| v as i32);

        // Optional arg: {"keyword_blocked_only": true} — scope batch scan to keyword-blocked media.
        let keyword_blocked_only = args
            .get("keyword_blocked_only")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        let classifier = match state.nsfw_classifier.as_ref() {
            Some(c) => c.clone(),
            None => {
                return Err(JobError::Other(
                    "NSFW classifier is not loaded on this worker — set POSTER_NSFW_ENABLED=true and provide the ONNX model file".into(),
                ));
            }
        };

        let model_ver = cfg.poster_nsfw_model_version.clone();
        let threshold = cfg.poster_nsfw_threshold;
        let batch_size = cfg.poster_nsfw_scan_batch as i64;
        let rpdb_key = cfg.rpdb_api_key.clone();

        // ── Single-row mode ───────────────────────────────────────────────────
        if let Some(media_id) = single_media_id {
            info!(media_id, model_ver, "poster_nsfw_scan: single-row classify");
            let rows = fetch_single_row(&state.pool_ro, &model_ver, media_id).await?;
            for row in &rows {
                classify_and_write(
                    row,
                    classifier.clone(),
                    &state.pool,
                    &state.http,
                    threshold,
                    &model_ver,
                    rpdb_key.as_deref(),
                )
                .await;
            }
            return Ok(());
        }

        // ── Batch mode (backfill) ─────────────────────────────────────────────
        info!(
            model_ver,
            threshold, batch_size, keyword_blocked_only, "poster_nsfw_scan: starting"
        );

        let mut total_classified = 0u64;
        let mut total_flagged = 0u64;
        let mut last_id: i32 = 0;

        loop {
            if ctx.is_cancelled() {
                info!(
                    total_classified,
                    total_flagged, "poster_nsfw_scan: cancelled — stopping early"
                );
                return Err(JobError::Cancelled);
            }

            let rows = fetch_unscanned_batch(
                &state.pool_ro,
                &model_ver,
                last_id,
                batch_size,
                keyword_blocked_only,
            )
            .await?;

            if rows.is_empty() {
                break;
            }

            let batch_start = Instant::now();
            let mut batch_classified = 0u32;
            let mut batch_flagged = 0u32;
            let mut batch_skipped = 0u32;

            for row in &rows {
                if ctx.is_cancelled() {
                    break;
                }

                last_id = last_id.max(row.media_id);

                // If there's no stored poster URL but we have an imdb_id + rpdb key,
                // we can still fetch from RPDB. Otherwise skip.
                let poster_url = match &row.poster_url {
                    Some(u) if !u.is_empty() => u.clone(),
                    _ => match (&row.imdb_id, &rpdb_key) {
                        (Some(imdb_id), Some(_)) if imdb_id.starts_with("tt") => {
                            String::new() // signal to use RPDB only
                        }
                        _ => {
                            mark_no_poster(&state.pool, row.media_id, &model_ver).await;
                            batch_skipped += 1;
                            continue;
                        }
                    },
                };

                let image_bytes = match fetch_poster_bytes(
                    &state.http,
                    row.imdb_id.as_deref(),
                    &poster_url,
                    rpdb_key.as_deref(),
                )
                .await
                {
                    Ok(b) => b,
                    Err(e) => {
                        warn!(
                            media_id = row.media_id,
                            url = poster_url,
                            "poster_nsfw_scan: fetch failed: {e}"
                        );
                        batch_skipped += 1;
                        continue;
                    }
                };

                let c = classifier.clone();
                let scores =
                    match tokio::task::spawn_blocking(move || c.classify(&image_bytes)).await {
                        Ok(Ok(s)) => s,
                        Ok(Err(e)) => {
                            warn!(
                                media_id = row.media_id,
                                "poster_nsfw_scan: inference failed: {e}"
                            );
                            batch_skipped += 1;
                            continue;
                        }
                        Err(e) => {
                            warn!(
                                media_id = row.media_id,
                                "poster_nsfw_scan: spawn_blocking panicked: {e}"
                            );
                            batch_skipped += 1;
                            continue;
                        }
                    };

                let score = scores.combined();
                let flagged = score >= threshold;

                if let Err(e) =
                    write_score(&state.pool, row.media_id, score, flagged, &model_ver).await
                {
                    warn!(
                        media_id = row.media_id,
                        "poster_nsfw_scan: DB write failed: {e}"
                    );
                }

                batch_classified += 1;
                if flagged {
                    batch_flagged += 1;
                }
            }

            let batch_ms = batch_start.elapsed().as_millis();
            let rss_mb = read_rss_kb() / 1024;
            total_classified += batch_classified as u64;
            total_flagged += batch_flagged as u64;

            info!(
                batch_classified,
                batch_flagged,
                batch_skipped,
                batch_ms,
                rss_mb,
                total_classified,
                total_flagged,
                "poster_nsfw_scan: batch done"
            );

            if rows.len() < batch_size as usize {
                break;
            }
        }

        info!(
            total_classified,
            total_flagged, "poster_nsfw_scan: complete"
        );
        Ok(())
    }
}

// ─── DB helpers ──────────────────────────────────────────────────────────────

struct MediaRow {
    media_id: i32,
    imdb_id: Option<String>,
    poster_url: Option<String>,
}

async fn fetch_unscanned_batch(
    pool: &sqlx::PgPool,
    model_ver: &str,
    after_id: i32,
    batch_size: i64,
    keyword_blocked_only: bool,
) -> Result<Vec<MediaRow>, JobError> {
    let kw_clause = if keyword_blocked_only {
        " AND m.is_keyword_blocked = true"
    } else {
        ""
    };
    let sql = format!(
        r#"
        SELECT m.id AS media_id,
               MAX(CASE WHEN mei.provider = 'imdb' THEN mei.external_id END) AS imdb_id,
               mi.url AS poster_url
        FROM media m
        LEFT JOIN media_external_id mei ON mei.media_id = m.id AND mei.provider = 'imdb'
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi ON true
        WHERE m.id > $1
          AND m.poster_nsfw_reviewed = false
          AND (m.poster_nsfw_model_ver IS NULL OR m.poster_nsfw_model_ver != $2)
          {kw_clause}
        GROUP BY m.id, mi.url
        ORDER BY m.id ASC
        LIMIT $3
        "#
    );
    let rows = sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
        .bind(after_id)
        .bind(model_ver)
        .bind(batch_size)
        .fetch_all(pool)
        .await?;

    Ok(rows
        .into_iter()
        .map(|r| MediaRow {
            media_id: r.try_get("media_id").unwrap_or(0),
            imdb_id: r.try_get("imdb_id").unwrap_or(None),
            poster_url: r.try_get("poster_url").unwrap_or(None),
        })
        .collect())
}

async fn write_score(
    pool: &sqlx::PgPool,
    media_id: i32,
    score: f32,
    flagged: bool,
    model_ver: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        UPDATE media
        SET poster_nsfw_score     = $2,
            poster_nsfw_flagged   = $3,
            poster_nsfw_model_ver = $4
        WHERE id = $1
          AND poster_nsfw_reviewed = false
        "#,
    )
    .bind(media_id)
    .bind(score as f64)
    .bind(flagged)
    .bind(model_ver)
    .execute(pool)
    .await?;
    Ok(())
}

async fn mark_no_poster(pool: &sqlx::PgPool, media_id: i32, model_ver: &str) {
    let _ = sqlx::query(
        "UPDATE media SET poster_nsfw_model_ver = $2 WHERE id = $1 AND poster_nsfw_reviewed = false",
    )
    .bind(media_id)
    .bind(model_ver)
    .execute(pool)
    .await;
}

// ─── HTTP fetch ──────────────────────────────────────────────────────────────

/// Fetch poster image bytes.
/// Priority: RPDB (if api_key + valid imdb_id) → stored poster URL → error.
async fn fetch_poster_bytes(
    http: &reqwest::Client,
    imdb_id: Option<&str>,
    fallback_url: &str,
    rpdb_key: Option<&str>,
) -> Result<Vec<u8>, JobError> {
    // Try RPDB first when we have an IMDb ID and API key.
    if let (Some(key), Some(imdb)) = (rpdb_key, imdb_id)
        && imdb.starts_with("tt")
    {
        let rpdb_url = format!(
            "https://api.ratingposterdb.com/{key}/imdb/poster-default/{imdb}.jpg?fallback=true"
        );
        match get_url(http, &rpdb_url).await {
            Ok(b) => return Ok(b),
            Err(e) => {
                // Log at debug and fall through to stored URL.
                tracing::debug!("RPDB fetch failed for {imdb}: {e}");
            }
        }
    }

    // Fallback to stored media_image URL.
    if !fallback_url.is_empty() {
        return get_url(http, fallback_url).await;
    }

    Err(JobError::Other("no poster source available".into()))
}

async fn get_url(http: &reqwest::Client, url: &str) -> Result<Vec<u8>, JobError> {
    let resp = http
        .get(url)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| JobError::Other(format!("HTTP get: {e}")))?;

    if !resp.status().is_success() {
        return Err(JobError::Other(format!(
            "HTTP {}: {url}",
            resp.status().as_u16()
        )));
    }

    let bytes = resp
        .bytes()
        .await
        .map_err(|e| JobError::Other(format!("HTTP body: {e}")))?;

    Ok(bytes.to_vec())
}

/// Fetch a single media row for import-time classification.
/// Returns an empty vec when the row has already been reviewed or scanned by the
/// current model version (so re-importing the same media is a no-op).
async fn fetch_single_row(
    pool: &sqlx::PgPool,
    model_ver: &str,
    media_id: i32,
) -> Result<Vec<MediaRow>, JobError> {
    let rows = sqlx::query(
        r#"
        SELECT m.id AS media_id,
               MAX(CASE WHEN mei.provider = 'imdb' THEN mei.external_id END) AS imdb_id,
               mi.url AS poster_url
        FROM media m
        LEFT JOIN media_external_id mei ON mei.media_id = m.id AND mei.provider = 'imdb'
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi ON true
        WHERE m.id = $1
          AND m.poster_nsfw_reviewed = false
          AND (m.poster_nsfw_model_ver IS NULL OR m.poster_nsfw_model_ver != $2)
        GROUP BY m.id, mi.url
        "#,
    )
    .bind(media_id)
    .bind(model_ver)
    .fetch_all(pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|r| MediaRow {
            media_id: r.try_get("media_id").unwrap_or(0),
            imdb_id: r.try_get("imdb_id").unwrap_or(None),
            poster_url: r.try_get("poster_url").unwrap_or(None),
        })
        .collect())
}

/// Classify a single `MediaRow` and persist the result.  Used by the single-row
/// import-time path; the batch loop inlines this logic to track per-batch counters.
async fn classify_and_write(
    row: &MediaRow,
    classifier: std::sync::Arc<crate::nsfw::NsfwClassifier>,
    pool: &sqlx::PgPool,
    http: &reqwest::Client,
    threshold: f32,
    model_ver: &str,
    rpdb_key: Option<&str>,
) {
    let poster_url = match &row.poster_url {
        Some(u) if !u.is_empty() => u.clone(),
        _ => match (&row.imdb_id, rpdb_key) {
            (Some(imdb_id), Some(_)) if imdb_id.starts_with("tt") => String::new(),
            _ => {
                mark_no_poster(pool, row.media_id, model_ver).await;
                return;
            }
        },
    };

    let image_bytes =
        match fetch_poster_bytes(http, row.imdb_id.as_deref(), &poster_url, rpdb_key).await {
            Ok(b) => b,
            Err(e) => {
                warn!(
                    media_id = row.media_id,
                    "poster_nsfw_scan: fetch failed: {e}"
                );
                return;
            }
        };

    let scores = match tokio::task::spawn_blocking(move || classifier.classify(&image_bytes)).await
    {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => {
            warn!(
                media_id = row.media_id,
                "poster_nsfw_scan: inference failed: {e}"
            );
            return;
        }
        Err(e) => {
            warn!(
                media_id = row.media_id,
                "poster_nsfw_scan: spawn_blocking panicked: {e}"
            );
            return;
        }
    };

    let score = scores.combined();
    let flagged = score >= threshold;
    if let Err(e) = write_score(pool, row.media_id, score, flagged, model_ver).await {
        warn!(
            media_id = row.media_id,
            "poster_nsfw_scan: DB write failed: {e}"
        );
    }
}
