//! Opt-in, reversible cleanup of unverified Telegram-only catalog stubs.
use async_trait::async_trait;
use serde::Deserialize;
use sqlx::{FromRow, PgPool, Postgres, QueryBuilder};
use tracing::info;

use crate::{
    cache,
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser::{parse_title, telegram_title::unresolved_rejection_reason},
};

pub struct TelegramCatalogCleanup;

#[derive(Debug, Deserialize)]
pub struct CleanupArgs {
    #[serde(default = "default_true")]
    pub dry_run: bool,
    #[serde(default)]
    pub after_id: i32,
    #[serde(default = "default_batch_size")]
    pub batch_size: i32,
}

fn default_true() -> bool {
    true
}
fn default_batch_size() -> i32 {
    500
}

// Protect provider-linked, manually created/reviewed, and mixed-source media.
// Reuse the predicate at write time so an enrichment after the scan is respected.
const ELIGIBLE: &str = "NOT m.is_blocked AND NOT m.is_user_created \
    AND m.created_by_user_id IS NULL AND m.last_refreshed_by_user_id IS NULL \
    AND NOT EXISTS (SELECT 1 FROM media_external_id e WHERE e.media_id = m.id) \
    AND EXISTS (SELECT 1 FROM stream_media_link l JOIN telegram_stream t ON t.stream_id = l.stream_id WHERE l.media_id = m.id) \
    AND NOT EXISTS (SELECT 1 FROM stream_media_link l LEFT JOIN telegram_stream t ON t.stream_id = l.stream_id \
        WHERE l.media_id = m.id AND (t.stream_id IS NULL OR l.is_verified OR l.linked_by_user_id IS NOT NULL)) \
    AND NOT EXISTS (SELECT 1 FROM file_media_link f WHERE f.media_id = m.id AND f.link_source NOT IN ('PTT_PARSER', 'FILENAME'))";

#[derive(Debug, FromRow)]
pub struct CleanupCandidate {
    pub id: i32,
    pub title: String,
    pub filenames: Vec<String>,
}

pub async fn scan_candidates(
    pool: &PgPool,
    after_id: i32,
    batch_size: i32,
) -> Result<Vec<CleanupCandidate>, sqlx::Error> {
    let mut query = QueryBuilder::<Postgres>::new(
        "SELECT m.id, m.title, ARRAY(SELECT COALESCE(t.file_name, '')::text FROM stream_media_link l \
         JOIN telegram_stream t ON t.stream_id = l.stream_id WHERE l.media_id = m.id ORDER BY t.stream_id) AS filenames \
         FROM media m WHERE m.id > ",
    );
    query
        .push_bind(after_id)
        .push(" AND ")
        .push(ELIGIBLE)
        .push(" ORDER BY m.id LIMIT ")
        .push_bind(batch_size.clamp(1, 5000));
    query.build_query_as().fetch_all(pool).await
}

pub fn rejection_reason(candidate: &CleanupCandidate) -> Option<&'static str> {
    // One credible release is enough to preserve the media.
    if candidate.filenames.is_empty() {
        return None;
    }
    let mut reason = None;
    for filename in &candidate.filenames {
        // Legacy rows without a filename cannot be judged safely.
        if filename.trim().is_empty() {
            return None;
        }
        reason = Some(unresolved_rejection_reason(
            filename,
            &parse_title(filename),
        )?);
    }
    reason
}

pub async fn block_candidate(
    pool: &PgPool,
    candidate: &CleanupCandidate,
    reason: &str,
) -> Result<bool, sqlx::Error> {
    let mut query = QueryBuilder::<Postgres>::new(
        "UPDATE media m SET is_blocked = true, blocked_at = NOW(), updated_at = NOW(), block_reason = ",
    );
    query
        .push_bind(format!("Telegram catalog quality: {reason}"))
        .push(" WHERE m.id = ")
        .push_bind(candidate.id)
        .push(" AND ")
        .push(ELIGIBLE)
        .push(
            " AND ARRAY(SELECT COALESCE(t.file_name, '')::text FROM stream_media_link l \
            JOIN telegram_stream t ON t.stream_id = l.stream_id \
            WHERE l.media_id = m.id ORDER BY t.stream_id) = ",
        )
        .push_bind(&candidate.filenames);
    let result = query.build().execute(pool).await?;
    Ok(result.rows_affected() > 0)
}

#[async_trait]
impl JobHandler for TelegramCatalogCleanup {
    const QUEUE: &'static str = "telegram_catalog_cleanup";
    const CONCURRENCY: usize = 1;
    type Args = CleanupArgs;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let mut after_id = args.after_id;
        let mut flagged = 0;
        let mut blocked = 0;
        loop {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }
            let rows = scan_candidates(&ctx.state.pool, after_id, args.batch_size).await?;
            if rows.is_empty() {
                break;
            }
            let mut changed = false;
            for row in &rows {
                if ctx.is_cancelled() {
                    break;
                }
                if let Some(reason) = rejection_reason(row) {
                    flagged += 1;
                    info!(media_id = row.id, title = %row.title, reason, dry_run = args.dry_run,
                        "telegram_catalog_cleanup: candidate");
                    if !args.dry_run && block_candidate(&ctx.state.pool, row, reason).await? {
                        blocked += 1;
                        changed = true;
                    }
                }
                after_id = row.id;
            }
            if changed {
                cache::invalidate_catalog_and_metadata_caches(&ctx.state.redis).await;
            }
        }
        info!(
            flagged,
            blocked,
            after_id,
            dry_run = args.dry_run,
            "telegram_catalog_cleanup: complete"
        );
        Ok(())
    }
}
