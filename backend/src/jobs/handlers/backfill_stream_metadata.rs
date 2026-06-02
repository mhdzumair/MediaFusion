//! PTT metadata backfill — re-parse `stream.name` and fill missing quality/language/HDR links.
//!
//! Run via worker CLI (processes all pages in one invocation by default):
//!   mediafusion-worker --run-job backfill_stream_metadata
//!
//! Args:
//!   page (default 0) — starting page when `continuous` is false
//!   page_size (default 500)
//!   only_missing (default true)
//!   stream_types (default ["TORRENT","USENET"])
//!   continuous (default true) — loop pages in-process until no rows remain
//!   max_pages (optional) — cap pages processed (for test runs)

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use tracing::info;

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
    #[serde(default)]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
    /// When true (default), only streams missing resolution/quality/links are selected.
    #[serde(default = "default_only_missing")]
    pub only_missing: bool,
    #[serde(default = "default_stream_types")]
    pub stream_types: Vec<String>,
    /// When true (default), process page after page in this job until done.
    #[serde(default = "default_continuous")]
    pub continuous: bool,
    /// Optional cap on pages processed (useful for dry runs).
    #[serde(default)]
    pub max_pages: Option<i64>,
}

fn default_page_size() -> i64 {
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
    const CONCURRENCY: usize = 2;
    type Args = BackfillStreamMetadataArgs;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let mut page = args.page;
        let page_size = args.page_size.max(1).min(5_000);
        let stream_types = parse_stream_types(&args.stream_types);
        let mut totals = StreamBackfillStats::default();
        let mut pages_done: i64 = 0;

        loop {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let offset = page * page_size;
            let rows = fetch_streams_for_backfill(
                &ctx.state.pool,
                &stream_types,
                args.only_missing,
                page_size,
                offset,
            )
            .await?;

            if rows.is_empty() {
                info!(
                    page,
                    pages_done,
                    examined = totals.examined,
                    linked_languages = totals.linked_languages,
                    updated_columns = totals.updated_columns,
                    "backfill_stream_metadata: complete — no more streams"
                );
                break;
            }

            info!(
                page,
                count = rows.len(),
                only_missing = args.only_missing,
                types = ?stream_types,
                "backfill_stream_metadata: processing page"
            );

            let mut page_stats = StreamBackfillStats::default();
            for chunk in rows.chunks(100) {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }
                let batch =
                    backfill_stream_batch(&ctx.state.pool, chunk, args.only_missing).await?;
                merge_stats(&mut page_stats, batch);
            }
            merge_stats(&mut totals, page_stats);

            info!(
                page,
                examined = totals.examined,
                updated_columns = totals.updated_columns,
                linked_languages = totals.linked_languages,
                linked_hdr = totals.linked_hdr,
                linked_audio = totals.linked_audio,
                linked_channels = totals.linked_channels,
                skipped_empty_parse = totals.skipped_empty_parse,
                "backfill_stream_metadata: page complete"
            );

            pages_done += 1;
            let full_page = rows.len() as i64 == page_size;

            if !args.continuous {
                if full_page {
                    let next = BackfillStreamMetadataArgs {
                        page: page + 1,
                        page_size,
                        only_missing: args.only_missing,
                        stream_types: args.stream_types.clone(),
                        continuous: false,
                        max_pages: args.max_pages,
                    };
                    enqueue_simple(&ctx.state.pool, Self::QUEUE, &next, EnqueueOpts::default())
                        .await?;
                }
                break;
            }

            if !full_page {
                break;
            }
            if args.max_pages.is_some_and(|max| pages_done >= max) {
                info!(pages_done, max = ?args.max_pages, "backfill_stream_metadata: stopped at max_pages");
                break;
            }
            page += 1;
        }

        Ok(())
    }
}
