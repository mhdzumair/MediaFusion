/// M3U playlist import job handler.
///
/// Job payload: `{"iptv_source_id": 123}`
///
/// Fetches the M3U URL stored on the IPTVSource row, parses the playlist, and
/// imports entries using the shared `import_tv_channel` helper (which is also
/// used by the HTTP route layer).
use async_trait::async_trait;
use serde::Deserialize;
use tracing::{info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    routes::content::{
        iptv_import::{self, IptvImportCtx},
        m3u_import::parse_m3u,
    },
};

pub struct M3uImport;

// ─── Payload ──────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct M3uImportArgs {
    pub iptv_source_id: i32,
}

// ─── DB row ───────────────────────────────────────────────────────────────────

#[allow(dead_code)]
struct IptvSourceRow {
    id: i32,
    user_id: i32,
    m3u_url: String,
    name: String,
    is_public: bool,
    import_live: bool,
    import_vod: bool,
    import_series: bool,
}

async fn fetch_source(
    pool: &sqlx::PgPool,
    source_id: i32,
) -> Result<Option<IptvSourceRow>, sqlx::Error> {
    let row = sqlx::query(
        r#"SELECT id, user_id, m3u_url, name, is_public, import_live, import_vod, import_series
           FROM iptv_source
           WHERE id = $1 AND source_type = 'M3U' AND is_active = true"#,
    )
    .bind(source_id)
    .fetch_optional(pool)
    .await?;

    let row = match row {
        Some(r) => r,
        None => return Ok(None),
    };

    use sqlx::Row;
    let m3u_url: Option<String> = row.try_get("m3u_url")?;
    let m3u_url = match m3u_url {
        Some(u) if !u.is_empty() => u,
        _ => return Ok(None), // No URL configured
    };

    Ok(Some(IptvSourceRow {
        id: row.try_get("id")?,
        user_id: row.try_get("user_id")?,
        m3u_url,
        name: row.try_get("name")?,
        is_public: row.try_get("is_public")?,
        import_live: row.try_get("import_live")?,
        import_vod: row.try_get("import_vod")?,
        import_series: row.try_get("import_series")?,
    }))
}

// ─── Handler ──────────────────────────────────────────────────────────────────

#[async_trait]
impl JobHandler for M3uImport {
    const QUEUE: &'static str = "m3u_import";
    const CONCURRENCY: usize = 2;
    type Args = M3uImportArgs;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let source = match fetch_source(&ctx.state.pool_ro, args.iptv_source_id).await? {
            Some(s) => s,
            None => {
                info!(
                    "m3u_import: source_id={} not found or inactive, nothing to do",
                    args.iptv_source_id
                );
                return Ok(());
            }
        };

        info!(
            "m3u_import: source_id={} name={:?} url={}",
            source.id, source.name, source.m3u_url
        );

        // Fetch M3U content
        let content = match ctx
            .state
            .http
            .get(&source.m3u_url)
            .timeout(std::time::Duration::from_secs(120))
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => r.text().await.map_err(JobError::Http)?,
            Ok(r) => {
                return Err(JobError::other(format!(
                    "m3u_import: HTTP {} from {}",
                    r.status(),
                    source.m3u_url
                )));
            }
            Err(e) => return Err(JobError::Http(e)),
        };

        let all_entries = parse_m3u(&content);
        let total_parsed = all_entries.len();

        info!(
            "m3u_import: source_id={} parsed {} entries",
            source.id, total_parsed
        );

        let source_label = format!("iptv:{}", source.id);
        let import_ctx = IptvImportCtx::from_state(&ctx.state);
        let user_id = source.user_id as i64;
        let is_public = source.is_public;
        let mut stats = iptv_import::IptvImportStats::default();

        for entry in &all_entries {
            if ctx.is_cancelled() {
                warn!("m3u_import: cancellation requested, stopping early");
                return Err(JobError::Cancelled);
            }

            let should_import = match entry.entry_type.as_str() {
                "tv" => source.import_live,
                "movie" => source.import_vod,
                "series" => source.import_series,
                _ => source.import_live,
            };

            if !should_import {
                stats.skipped += 1;
                continue;
            }

            let result = match entry.entry_type.as_str() {
                "tv" => {
                    let r = iptv_import::import_tv_entry(
                        &ctx.state.pool,
                        entry,
                        &source_label,
                        user_id,
                        is_public,
                    )
                    .await;
                    if r.stream_created {
                        stats.tv += 1;
                    } else if r.stream_existed {
                        stats.skipped += 1;
                    } else {
                        stats.failed += 1;
                    }
                    Ok(())
                }
                "movie" => iptv_import::import_movie_entry(
                    &import_ctx,
                    entry,
                    &source_label,
                    user_id,
                    is_public,
                )
                .await
                .map(|created| {
                    if created {
                        stats.movie += 1;
                    } else {
                        stats.skipped += 1;
                    }
                }),
                "series" => iptv_import::import_series_entry(
                    &import_ctx,
                    entry,
                    &source_label,
                    user_id,
                    is_public,
                )
                .await
                .map(|created| {
                    if created {
                        stats.series += 1;
                    } else {
                        stats.skipped += 1;
                    }
                }),
                _ => Ok(()),
            };

            if let Err(e) = result {
                warn!("m3u_import: entry {:?} failed: {e}", entry.name);
                stats.failed += 1;
            }
        }

        let sync_stats = serde_json::json!({
            "tv": stats.tv,
            "movie": stats.movie,
            "series": stats.series,
            "failed": stats.failed,
            "skipped": stats.skipped,
            "total_parsed": total_parsed,
        });

        sqlx::query(
            "UPDATE iptv_source SET last_synced_at = NOW(), last_sync_stats = $1::jsonb WHERE id = $2",
        )
        .bind(&sync_stats)
        .bind(source.id)
        .execute(&ctx.state.pool)
        .await?;

        info!(
            "m3u_import: source_id={} done — parsed={} tv={} movie={} series={} skipped={} failed={}",
            source.id, total_parsed, stats.tv, stats.movie, stats.series, stats.skipped, stats.failed
        );

        Ok(())
    }
}
