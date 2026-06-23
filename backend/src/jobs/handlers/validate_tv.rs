use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tracing::{info, warn};

use crate::jobs::{
    enqueue::{EnqueueOpts, enqueue_simple},
    error::JobError,
    handler::{JobCtx, JobHandler},
};

pub struct ValidateTvStreams;

#[derive(Debug, Deserialize, Serialize)]
pub struct ValidateTvArgs {
    #[serde(default)]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
}

fn default_page_size() -> i64 {
    100
}

/// A single row returned from the DB stream query.
struct StreamRow {
    id: i32,
    url: String,
    name: Option<String>,
}

/// Fetch a page of HTTP streams linked to TV media.
async fn fetch_page(
    pool: &sqlx::PgPool,
    limit: i64,
    offset: i64,
) -> Result<Vec<StreamRow>, JobError> {
    let rows = sqlx::query(
        r#"
        SELECT s.id, hs.url, s.name
        FROM stream s
        JOIN http_stream hs ON hs.stream_id = s.id
        JOIN stream_media_link sml ON sml.stream_id = s.id
        JOIN media m ON m.id = sml.media_id
        WHERE m.type = 'TV'
          AND NOT s.is_blocked
          AND NOT s.is_keyword_blocked
        ORDER BY s.updated_at ASC
        LIMIT $1 OFFSET $2
        "#,
    )
    .bind(limit)
    .bind(offset)
    .fetch_all(pool)
    .await?;

    let mut result = Vec::with_capacity(rows.len());
    for row in rows {
        use sqlx::Row;
        result.push(StreamRow {
            id: row.try_get("id")?,
            url: row.try_get("url")?,
            name: row.try_get("name")?,
        });
    }
    Ok(result)
}

/// HEAD-probe a single URL. Returns true if the server responds with status < 500.
async fn probe_url(http: &reqwest::Client, url: &str) -> bool {
    match http.head(url).timeout(Duration::from_secs(10)).send().await {
        Ok(resp) => resp.status().as_u16() < 500,
        Err(_) => false,
    }
}

/// Bulk-update is_active for a batch of streams.
async fn bulk_update(
    pool: &sqlx::PgPool,
    ids: &[i32],
    active_flags: &[bool],
) -> Result<(), JobError> {
    sqlx::query(
        r#"
        UPDATE stream SET is_active = results.is_active, updated_at = NOW()
        FROM (SELECT UNNEST($1::int[]) AS id, UNNEST($2::bool[]) AS is_active) AS results
        WHERE stream.id = results.id
        "#,
    )
    .bind(ids)
    .bind(active_flags)
    .execute(pool)
    .await?;
    Ok(())
}

#[async_trait]
impl JobHandler for ValidateTvStreams {
    const QUEUE: &'static str = "validate_tv";
    const CONCURRENCY: usize = 4;
    type Args = ValidateTvArgs;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let page = args.page;
        let page_size = args.page_size;
        let offset = page * page_size;

        let rows = fetch_page(&ctx.state.pool_ro, page_size, offset).await?;

        if rows.is_empty() {
            info!("validate_tv: page={page} — no more streams, done");
            return Ok(());
        }

        info!("validate_tv: page={page} — probing {} streams", rows.len());

        // Probe all streams in this page.
        let mut ids: Vec<i32> = Vec::with_capacity(rows.len());
        let mut active_flags: Vec<bool> = Vec::with_capacity(rows.len());

        for row in &rows {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let is_active = probe_url(&ctx.state.http, &row.url).await;
            let stream_name = row.name.as_deref().unwrap_or("<unnamed>");
            if !is_active {
                warn!(
                    "validate_tv: stream id={} name={stream_name} url={} -> inactive",
                    row.id, row.url
                );
            }
            ids.push(row.id);
            active_flags.push(is_active);
        }

        // Write results back to DB.
        bulk_update(&ctx.state.pool, &ids, &active_flags).await?;

        let active_count = active_flags.iter().filter(|&&a| a).count();
        info!(
            "validate_tv: page={page} — updated {} streams ({active_count} active, {} inactive)",
            ids.len(),
            ids.len() - active_count
        );

        // Enqueue the next page.
        let next_args = ValidateTvArgs {
            page: page + 1,
            page_size,
        };
        enqueue_simple(
            &ctx.state.pool,
            ValidateTvStreams::QUEUE,
            &next_args,
            EnqueueOpts::default(),
        )
        .await?;

        Ok(())
    }
}
