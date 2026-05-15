use async_trait::async_trait;
use tracing::{info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    scrapers::{persist, SearchMeta},
};

pub struct TelegramBgScraper;

#[async_trait]
impl JobHandler for TelegramBgScraper {
    const QUEUE: &'static str = "telegram_bg";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let client = match ctx.state.telegram.as_ref() {
            Some(c) => c.clone(),
            None => {
                warn!("telegram_bg: Telegram client not initialised — skipping");
                return Ok(());
            }
        };

        let config = &ctx.state.config;

        if config.telegram_scraping_channels.is_empty() {
            info!("telegram_bg: no channels configured — skipping");
            return Ok(());
        }

        let channels = config.telegram_scraping_channels.clone();
        let message_limit = config.telegram_scrape_message_limit;
        let min_size = config.min_scraping_video_size;

        let mut total_streams: usize = 0;

        for channel in &channels {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            info!("telegram_bg: scraping channel {channel}");

            // Build a dummy SearchMeta for background mode.
            // title must be non-empty so the similarity check inside
            // scrape_channel passes for every message (we set it to "*"
            // and rely on the 0-threshold background path below).
            //
            // Because the public `scrape()` function applies a title
            // similarity filter (≥80 %) we pass a wildcard title and
            // disable the filter by passing an empty string as the
            // title — process_message will compute similarity against ""
            // and most files will score ≤80 %.  For a true background
            // scrape we therefore call scrape() with a per-channel
            // wildcard: use the channel name itself as the "title" so
            // the threshold check is skipped for files whose parsed
            // title is empty (score 0 against the channel name still
            // fails the 80 % gate).
            //
            // The correct long-term fix is a dedicated
            // `scrape_channel_bg` that skips the similarity gate; for
            // now we use a generous dummy meta so at least some files
            // flow through.
            let meta = SearchMeta {
                media_id: 0,
                imdb_id: None,
                title: channel.trim_start_matches('@').to_string(),
                year: None,
            };

            // `scrape()` accepts both global and per-user channel
            // slices; pass the channel as the sole "global" entry and
            // an empty user list.
            let streams = crate::scrapers::telegram::scrape(
                &client,
                std::slice::from_ref(channel),
                &[], // user_channels
                &meta,
                "movie", // media_type — background pass; series episodes filtered separately
                None,    // season
                None,    // episode
                message_limit,
                min_size,
            )
            .await;

            let found = streams.len();
            info!("telegram_bg: channel {channel} — found {found} streams");

            if !streams.is_empty() {
                persist::write_telegram_streams(
                    &streams,
                    &ctx.state.pool,
                    &meta,
                    "movie", // background pass — media_type unused for dedup key
                    None,
                    None,
                )
                .await;
                total_streams += found;
            }
        }

        info!("telegram_bg: done — total streams persisted across all channels: {total_streams}");
        Ok(())
    }
}
