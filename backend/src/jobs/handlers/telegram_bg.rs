use async_trait::async_trait;
use tracing::{info, warn};

use crate::{
    bot::BotApi,
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

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let client = match ctx.state.telegram.as_ref() {
            Some(c) => c.clone(),
            None => {
                warn!("telegram_bg: Telegram client not initialised — skipping");
                return Ok(());
            }
        };

        let config = &ctx.state.config;
        let message_limit = config.telegram_scrape_message_limit;
        let min_size = config.min_scraping_video_size;

        // Per-user on-demand scrape from /scrape bot command
        if let Some(channel) = args.get("channel").and_then(|v| v.as_str()) {
            return run_user_channel_scrape(&ctx, &client, &args, channel, message_limit, min_size)
                .await;
        }

        if config.telegram_scraping_channels.is_empty() {
            info!("telegram_bg: no channels configured — skipping");
            return Ok(());
        }

        let channels = config.telegram_scraping_channels.clone();
        let mut total_streams: usize = 0;

        for channel in &channels {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }
            total_streams +=
                scrape_and_persist_channel(&ctx, &client, channel, message_limit, min_size).await;
        }

        info!("telegram_bg: done — total streams persisted across all channels: {total_streams}");
        Ok(())
    }
}

async fn run_user_channel_scrape(
    ctx: &JobCtx,
    client: &grammers_client::Client,
    args: &serde_json::Value,
    channel: &str,
    message_limit: i32,
    min_size: u64,
) -> Result<(), JobError> {
    let telegram_user_id = args
        .get("telegram_user_id")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let chat_id = args.get("chat_id").and_then(|v| v.as_i64()).unwrap_or(0);
    let progress_message_id = args.get("progress_message_id").and_then(|v| v.as_i64());

    let api = BotApi::from_state(&ctx.state).ok();
    if let (Some(api), Some(mid)) = (api.as_ref(), progress_message_id) {
        let _ = api
            .edit_message_text(
                chat_id,
                mid,
                &format!("🔍 *Scraping Channel*\n\n`{channel}`\n\n⏳ Fetching messages..."),
                None,
            )
            .await;
    }

    let persisted = scrape_and_persist_channel(ctx, client, channel, message_limit, min_size).await;

    if let (Some(api), Some(mid)) = (api.as_ref(), progress_message_id) {
        let _ = api
            .edit_message_text(
                chat_id,
                mid,
                &format!(
                    "✅ *Scrape Complete*\n\nChannel: `{channel}`\nStreams saved: {persisted}"
                ),
                None,
            )
            .await;
    }

    crate::bot::clear_scrape_job(&ctx.state, telegram_user_id).await;
    Ok(())
}

async fn scrape_and_persist_channel(
    ctx: &JobCtx,
    client: &grammers_client::Client,
    channel: &str,
    message_limit: i32,
    min_size: u64,
) -> usize {
    info!("telegram_bg: scraping channel {channel}");

    let channel_title = channel.trim_start_matches('@');
    let probe_meta = crate::scrapers::SearchMeta {
        media_id: crate::db::MediaId(0),
        imdb_id: None,
        title: channel_title.to_string(),
        year: None,
    };

    let streams = crate::scrapers::telegram::scrape(
        client,
        &[channel.to_string()],
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
    persisted
}
