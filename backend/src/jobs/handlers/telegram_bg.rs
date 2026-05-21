use async_trait::async_trait;
use tracing::{info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    scrapers::{media_resolve, persist},
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

            let channel_title = channel.trim_start_matches('@');
            let probe_meta = crate::scrapers::SearchMeta {
                media_id: 0,
                imdb_id: None,
                title: channel_title.to_string(),
                year: None,
            };

            let streams = crate::scrapers::telegram::scrape(
                &client,
                std::slice::from_ref(channel),
                &[],
                &probe_meta,
                "movie",
                None,
                None,
                message_limit,
                min_size,
            )
            .await;

            let cfg = &ctx.state.config;
            let mut persisted = 0usize;
            for stream in &streams {
                let title = stream
                    .parsed
                    .title
                    .as_deref()
                    .filter(|t| !t.is_empty())
                    .unwrap_or(&stream.name);
                let is_series = stream.season.is_some() || stream.episode.is_some();
                let media_type = if is_series { "series" } else { "movie" };
                if let Some(meta) = media_resolve::search_meta_for_title_with_anime(
                    &ctx.state.pool,
                    &ctx.state.http,
                    title,
                    stream.parsed.year,
                    is_series,
                    cfg.tmdb_api_key.as_deref(),
                    cfg.imdb_cinemeta_fallback_enabled,
                    &cfg.anime_metadata_source_order,
                    &cfg.metadata_primary_source,
                )
                .await
                {
                    persist::write_telegram_streams(
                        std::slice::from_ref(stream),
                        &ctx.state.pool,
                        &meta,
                        media_type,
                        stream.season,
                        stream.episode,
                    )
                    .await;
                    persisted += 1;
                }
            }

            info!(
                "telegram_bg: channel {channel} — persisted {persisted}/{}",
                streams.len()
            );
            total_streams += persisted;
        }

        info!("telegram_bg: done — total streams persisted across all channels: {total_streams}");
        Ok(())
    }
}
