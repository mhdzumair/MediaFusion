//! PTT metadata backfill — re-parse `stream.name` and fill missing quality/language/HDR links.
//!
//! Run via worker CLI (processes all batches in one invocation by default):
//!   mediafusion-worker --run-job backfill_stream_metadata
//!
//! Args:
//!   after_id (default 0) — resume from this stream id (keyset pagination)
//!   batch_size (default 500) — streams per batch (max 5000)
//!   only_missing (default true)
//!   stream_types (default ["TORRENT","USENET"])
//!   continuous (default true) — loop batches in-process until no rows remain
//!   max_batches (optional) — cap batches processed (for test runs)

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

use crate::db::stream_backfill::{
    backfill_stream_batch, default_backfill_stream_types, fetch_streams_for_backfill,
    StreamBackfillStats,
};
use crate::db::types::StreamType;
use crate::jobs::{
    enqueue::{enqueue_simple, EnqueueOpts},
    error::JobError,
    handler::{JobCtx, JobHandler},
};

pub struct BackfillStreamMetadata;

#[derive(Debug, Deserialize, Serialize)]
pub struct BackfillStreamMetadataArgs {
    /// Resume scanning after this stream id (keyset pagination).
    #[serde(default)]
    pub after_id: i32,
    #[serde(default = "default_batch_size", alias = "page_size")]
    pub batch_size: i64,
    /// When true (default), only streams missing resolution/quality/links are selected.
    #[serde(default = "default_only_missing")]
    pub only_missing: bool,
    #[serde(default = "default_stream_types")]
    pub stream_types: Vec<String>,
    /// When true (default), process batch after batch in this job until done.
    #[serde(default = "default_continuous")]
    pub continuous: bool,
    /// Optional cap on batches processed (useful for dry runs).
    #[serde(default, alias = "max_pages")]
    pub max_batches: Option<i64>,
    /// Deprecated: offset pagination was removed; use `after_id` to resume.
    #[serde(default)]
    pub page: Option<i64>,
}

fn default_batch_size() -> i64 {
    500
}

fn default_only_missing() -> bool {
    true
}

fn default_continuous() -> bool {
    true
}

fn default_stream_types() -> Vec<String> {
    vec!["TORRENT".into(), "USENET".into()]
}

fn parse_stream_types(labels: &[String]) -> Vec<StreamType> {
    let mut out = Vec::new();
    for label in labels {
        if let Some(t) = StreamType::from_wire(label) {
            out.push(t);
        }
    }
    if out.is_empty() {
        default_backfill_stream_types()
    } else {
        out
    }
}

fn merge_stats(acc: &mut StreamBackfillStats, batch: StreamBackfillStats) {
    acc.examined += batch.examined;
    acc.updated_columns += batch.updated_columns;
    acc.linked_languages += batch.linked_languages;
    acc.linked_hdr += batch.linked_hdr;
    acc.linked_audio += batch.linked_audio;
    acc.linked_channels += batch.linked_channels;
    acc.skipped_empty_parse += batch.skipped_empty_parse;
}

#[async_trait]
impl JobHandler for BackfillStreamMetadata {
    const QUEUE: &'static str = "backfill_stream_metadata";
    const CONCURRENCY: usize = 1;
    type Args = BackfillStreamMetadataArgs;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        if args.page.is_some() {
            warn!(
                page = ?args.page,
                "backfill_stream_metadata: `page` is deprecated; use `after_id` for resumable keyset pagination"
            );
        }

        let mut after_id = args.after_id;
        let batch_size = args.batch_size.clamp(1, 5_000);
        let stream_types = parse_stream_types(&args.stream_types);
        let mut totals = StreamBackfillStats::default();
        let mut batches_done: i64 = 0;

        loop {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let rows = fetch_streams_for_backfill(
                &ctx.state.pool,
                &stream_types,
                args.only_missing,
                batch_size,
                after_id,
            )
            .await?;

            if rows.is_empty() {
                info!(
                    after_id,
                    batches_done,
                    examined = totals.examined,
                    linked_languages = totals.linked_languages,
                    updated_columns = totals.updated_columns,
                    "backfill_stream_metadata: complete — no more streams"
                );
                break;
            }

            let batch_last_id = rows.last().map(|row| row.id).unwrap_or(after_id);

            info!(
                after_id,
                batch_last_id,
                count = rows.len(),
                only_missing = args.only_missing,
                types = ?stream_types,
                "backfill_stream_metadata: processing batch"
            );

            let mut batch_stats = StreamBackfillStats::default();
            for chunk in rows.chunks(100) {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }
                let batch =
                    backfill_stream_batch(&ctx.state.pool, chunk, args.only_missing).await?;
                merge_stats(&mut batch_stats, batch);
            }
            merge_stats(&mut totals, batch_stats);

            info!(
                after_id,
                batch_last_id,
                examined = totals.examined,
                updated_columns = totals.updated_columns,
                linked_languages = totals.linked_languages,
                linked_hdr = totals.linked_hdr,
                linked_audio = totals.linked_audio,
                linked_channels = totals.linked_channels,
                skipped_empty_parse = totals.skipped_empty_parse,
                "backfill_stream_metadata: batch complete"
            );

            batches_done += 1;
            let full_batch = rows.len() as i64 == batch_size;
            after_id = batch_last_id;

            if !args.continuous {
                if full_batch {
                    let next = BackfillStreamMetadataArgs {
                        after_id,
                        batch_size,
                        only_missing: args.only_missing,
                        stream_types: args.stream_types.clone(),
                        continuous: false,
                        max_batches: args.max_batches,
                        page: None,
                    };
                    enqueue_simple(&ctx.state.pool, Self::QUEUE, &next, EnqueueOpts::default())
                        .await?;
                }
                break;
            }

            if !full_batch {
                break;
            }
            if args.max_batches.is_some_and(|max| batches_done >= max) {
                info!(
                    batches_done,
                    after_id,
                    max = ?args.max_batches,
                    "backfill_stream_metadata: stopped at max_batches"
                );
                break;
            }
        }

        Ok(())
    }
}
