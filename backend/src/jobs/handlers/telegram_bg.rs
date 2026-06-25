use async_trait::async_trait;
use tracing::{info, warn};

use crate::{
    bot::BotApi,
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    scrapers::{media_resolve, stream_convert},
};

pub struct TelegramBgScraper;

#[derive(Default)]
struct ScrapeMetrics {
    imported: usize,
    skipped: usize,
    errors: usize,
}

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
            let metrics =
                scrape_and_persist_channel(&ctx, &client, channel, message_limit, min_size).await;
            total_streams += metrics.imported;
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

    let metrics = scrape_and_persist_channel(ctx, client, channel, message_limit, min_size).await;

    if let (Some(api), Some(mid)) = (api.as_ref(), progress_message_id) {
        let _ = api
            .edit_message_text(
                chat_id,
                mid,
                &format!(
                    "✅ *Scrape Complete*\n\nChannel: `{channel}`\n\
                     📊 Results:\n\
                     • Imported: {}\n\
                     • Skipped: {}\n\
                     • Errors: {}",
                    metrics.imported, metrics.skipped, metrics.errors
                ),
                None,
            )
            .await;
    }

    if let Some(notification_chat_id) = ctx.state.config.telegram_chat_id.as_deref()
        && !notification_chat_id.is_empty()
    {
        let summary = format!(
            "📡 *Channel Scrape Completed*\n\n\
                 Channel: `{channel}`\n\
                 Submitted by: user `{telegram_user_id}`\n\
                 • Imported: {}\n\
                 • Skipped: {}\n\
                 • Errors: {}",
            metrics.imported, metrics.skipped, metrics.errors
        );
        if let Some(api) = api.as_ref() {
            let _ = api
                .send_message(
                    notification_chat_id.parse().unwrap_or(chat_id),
                    &summary,
                    None,
                )
                .await;
        }
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
) -> ScrapeMetrics {
    info!("telegram_bg: scraping channel {channel}");

    let probe_meta = crate::scrapers::SearchMeta {
        media_id: crate::db::MediaId(0),
        imdb_id: None,
        title: String::new(),
        year: None,
    };

    let kf = ctx
        .state
        .keyword_filters
        .read()
        .map(|g| g.clone())
        .unwrap_or_default();
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
        &kf,
    )
    .await;

    let cfg = &ctx.state.config;
    let mut metrics = ScrapeMetrics::default();

    for stream in &streams {
        let title = stream
            .parsed
            .title
            .as_deref()
            .filter(|t| !t.is_empty())
            .unwrap_or(&stream.name);

        let exists: Option<(i32,)> = sqlx::query_as(
            "SELECT stream_id FROM telegram_stream WHERE chat_id = $1 AND message_id = $2 LIMIT 1",
        )
        .bind(stream.chat_id.to_string())
        .bind(stream.message_id)
        .fetch_optional(&ctx.state.pool)
        .await
        .unwrap_or(None);

        if exists.is_some() {
            metrics.skipped += 1;
            continue;
        }

        let is_series = stream.season.is_some() || stream.episode.is_some();
        let media_type = if is_series { "series" } else { "movie" };

        let meta_result = media_resolve::search_meta_for_telegram_feed(
            &ctx.state.pool,
            &ctx.state.http,
            title,
            stream.parsed.year,
            is_series,
            stream.caption_imdb_id.as_deref(),
            cfg.tmdb_api_key.as_deref(),
            cfg.tvdb_api_key.as_deref(),
            cfg.imdb_cinemeta_fallback_enabled,
            &cfg.anime_metadata_source_order,
            &cfg.metadata_primary_source,
        )
        .await;

        match meta_result {
            Some(meta) => {
                if stream_convert::write_back_telegram(
                    &ctx.state.pool,
                    std::slice::from_ref(stream),
                    &meta,
                    media_type,
                    stream.season,
                    stream.episode,
                )
                .await
                {
                    metrics.imported += 1;
                } else {
                    metrics.skipped += 1;
                }
            }
            None => metrics.skipped += 1,
        }
    }

    info!(
        "telegram_bg: channel {channel} — imported {}/skipped {}/errors {} (of {} candidates)",
        metrics.imported,
        metrics.skipped,
        metrics.errors,
        streams.len()
    );
    metrics
}
