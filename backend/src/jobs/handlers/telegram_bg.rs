use async_trait::async_trait;
use tracing::{info, warn};

use crate::{
    bot::BotApi,
    db::types::UserId,
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    scrapers::{
        media_resolve, stream_convert,
        telegram::{self, DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT, format_scrape_message_limit},
    },
};

pub struct TelegramBgScraper;

#[derive(Default, Clone)]
struct ScrapeMetrics {
    imported: usize,
    skipped: usize,
    errors: usize,
}

#[derive(Clone)]
struct ChannelScrapeResult {
    channel: String,
    metrics: ScrapeMetrics,
}

#[async_trait]
impl JobHandler for TelegramBgScraper {
    const QUEUE: &'static str = "telegram_bg";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        if !ctx.state.telegram_clients.api_configured() {
            warn!("telegram_bg: Telegram API credentials not configured — skipping");
            return Ok(());
        }

        let message_limit = parse_job_message_limit(&args);
        let min_size = ctx.state.config.min_scraping_video_size;
        let limit_label = format_scrape_message_limit(message_limit);
        let api = BotApi::from_state(&ctx.state).ok();
        if api.is_none() {
            warn!("telegram_bg: bot API not configured — scrape summaries will not be sent");
        }

        // Per-user on-demand scrape from bot or web UI
        if args.get("mediafusion_user_id").is_some() {
            return run_user_scrape(&ctx, &args, message_limit, min_size, api.as_ref()).await;
        }

        let targets = crate::db::user_telegram_session::list_scrape_targets(&ctx.state.pool).await;
        if targets.is_empty() {
            info!("telegram_bg: no users with sessions and channels — skipping");
            return Ok(());
        }

        let mut total_streams: usize = 0;
        for target in targets {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }
            let Some(client) = ctx
                .state
                .telegram_clients
                .get_client(&ctx.state.pool, target.user_id)
                .await
            else {
                warn!(
                    "telegram_bg: could not load client for user {}",
                    target.user_id.0
                );
                continue;
            };

            let mut totals = ScrapeMetrics::default();
            let mut channel_results = Vec::with_capacity(target.channels.len());

            for channel in &target.channels {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }
                let metrics =
                    scrape_and_persist_channel(&ctx, &client, channel, message_limit, min_size)
                        .await;
                totals.imported += metrics.imported;
                totals.skipped += metrics.skipped;
                totals.errors += metrics.errors;
                channel_results.push(ChannelScrapeResult {
                    channel: channel.clone(),
                    metrics,
                });
            }

            total_streams += totals.imported;

            send_scrape_notifications(
                &ctx,
                api.as_ref(),
                &channel_results,
                &totals,
                &limit_label,
                target.user_id,
                None,
                None,
                None,
            )
            .await;
        }

        info!("telegram_bg: done — total streams persisted across all users: {total_streams}");
        Ok(())
    }
}

fn parse_job_message_limit(args: &serde_json::Value) -> Option<i32> {
    parse_channel_message_limit(args, None)
}

fn parse_channel_message_limit(args: &serde_json::Value, channel: Option<&str>) -> Option<i32> {
    if let Some(channel) = channel
        && let Some(channel_cfg) = args
            .get("channel_limits")
            .and_then(|v| v.get(channel))
    {
        if channel_cfg
            .get("scrape_all_messages")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            return None;
        }
        if let Some(limit) = channel_cfg
            .get("message_limit")
            .and_then(|v| v.as_i64())
            .map(|n| n as i32)
        {
            return Some(limit);
        }
    }

    if args
        .get("scrape_all_messages")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        return None;
    }

    args.get("message_limit")
        .and_then(|v| v.as_i64())
        .map(|n| n as i32)
        .or_else(|| {
            args.get("message_limit")
                .and_then(|v| v.as_str())
                .and_then(|s| telegram::parse_scrape_message_limit(s).ok())
                .flatten()
        })
        .or(Some(DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT))
}

async fn run_user_scrape(
    ctx: &JobCtx,
    args: &serde_json::Value,
    message_limit: Option<i32>,
    min_size: u64,
    api: Option<&BotApi>,
) -> Result<(), JobError> {
    let telegram_user_id = args
        .get("telegram_user_id")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let mediafusion_user_id = args
        .get("mediafusion_user_id")
        .and_then(|v| v.as_i64())
        .map(|id| UserId(id as i32))
        .ok_or_else(|| JobError::other("missing mediafusion_user_id"))?;
    let chat_id = args.get("chat_id").and_then(|v| v.as_i64());
    let progress_message_id = args.get("progress_message_id").and_then(|v| v.as_i64());
    let scrape_all = args
        .get("scrape_all")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    let channels = if scrape_all {
        crate::db::telegram_channels::user_scraping_channels(&ctx.state.pool, mediafusion_user_id)
            .await
    } else {
        args.get("channel")
            .and_then(|v| v.as_str())
            .map(|channel| vec![channel.to_string()])
            .unwrap_or_default()
    };

    if channels.is_empty() {
        return Ok(());
    }

    let Some(client) = ctx
        .state
        .telegram_clients
        .get_client(&ctx.state.pool, mediafusion_user_id)
        .await
    else {
        warn!(
            "telegram_bg: user {} has no Telegram scraping session",
            mediafusion_user_id.0
        );
        return Ok(());
    };

    let owned_api = if api.is_some() {
        None
    } else {
        BotApi::from_state(&ctx.state).ok()
    };
    let api = api.or(owned_api.as_ref());
    let mut totals = ScrapeMetrics::default();
    let mut channel_results = Vec::with_capacity(channels.len());
    let limit_label = format_scrape_message_limit(message_limit);

    for (index, channel) in channels.iter().enumerate() {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        if let (Some(api), Some(mid), Some(chat_id)) = (api, progress_message_id, chat_id)
        {
            let channel_limit = parse_channel_message_limit(args, Some(channel));
            let channel_limit_label = format_scrape_message_limit(channel_limit);
            let progress = if channels.len() == 1 {
                format!(
                    "🔍 *Scraping Channel*\n\n`{channel}`\nDepth: {channel_limit_label}\n\n⏳ Fetching messages..."
                )
            } else {
                format!(
                    "🔍 *Scraping Channels* ({}/{total})\n\n`{channel}`\nDepth: {channel_limit_label}\n\n⏳ Fetching messages...",
                    index + 1,
                    total = channels.len()
                )
            };
            let _ = api.edit_message_text(chat_id, mid, &progress, None).await;
        }

        let channel_limit = parse_channel_message_limit(args, Some(channel));
        let metrics = scrape_and_persist_channel(
            ctx,
            &client,
            channel,
            channel_limit,
            min_size,
        )
        .await;
        totals.imported += metrics.imported;
        totals.skipped += metrics.skipped;
        totals.errors += metrics.errors;
        channel_results.push(ChannelScrapeResult {
            channel: channel.clone(),
            metrics,
        });
    }

    send_scrape_notifications(
        ctx,
        api,
        &channel_results,
        &totals,
        &limit_label,
        mediafusion_user_id,
        Some(telegram_user_id),
        chat_id,
        progress_message_id,
    )
    .await;

    if telegram_user_id > 0 {
        crate::bot::clear_scrape_job(&ctx.state, telegram_user_id).await;
    }
    Ok(())
}

async fn send_scrape_notifications(
    ctx: &JobCtx,
    api: Option<&BotApi>,
    channel_results: &[ChannelScrapeResult],
    totals: &ScrapeMetrics,
    limit_label: &str,
    mediafusion_user_id: UserId,
    telegram_user_id: Option<i64>,
    chat_id: Option<i64>,
    progress_message_id: Option<i64>,
) {
    let Some(api) = api else {
        warn!(
            "telegram_bg: bot API not configured — skipping scrape summary for user {}",
            mediafusion_user_id.0
        );
        return;
    };

    let telegram_user_id = telegram_user_id.unwrap_or(0);
    let summary = build_scrape_summary(
        channel_results,
        totals,
        limit_label,
        telegram_user_id,
        mediafusion_user_id,
    );

    if let (Some(mid), Some(chat_id)) = (progress_message_id, chat_id) {
        match api.edit_message_text(chat_id, mid, &summary, None).await {
            Ok(()) => info!(
                "telegram_bg: updated scrape summary for user {} in chat {chat_id}",
                mediafusion_user_id.0
            ),
            Err(e) => warn!(
                "telegram_bg: failed to update scrape summary for user {} in chat {chat_id}: {e}",
                mediafusion_user_id.0
            ),
        }
    } else {
        let notify_chat_id = chat_id.or(if telegram_user_id > 0 {
            Some(telegram_user_id)
        } else {
            crate::db::telegram::get_user_telegram_id(&ctx.state.pool_ro, mediafusion_user_id).await
        });
        match notify_chat_id {
            Some(target_chat_id) => match api.send_message(target_chat_id, &summary, None).await {
                Ok(_) => info!(
                    "telegram_bg: sent scrape summary to chat {target_chat_id} for user {}",
                    mediafusion_user_id.0
                ),
                Err(e) => warn!(
                    "telegram_bg: failed to send scrape summary to chat {target_chat_id} for user {}: {e}",
                    mediafusion_user_id.0
                ),
            },
            None => warn!(
                "telegram_bg: no Telegram chat id for user {} — scrape summary not sent",
                mediafusion_user_id.0
            ),
        }
    }

    let Some(notification_chat_id) = ctx.state.config.telegram_chat_id.as_deref() else {
        return;
    };
    if notification_chat_id.is_empty() {
        return;
    }

    let notify_user_id = if telegram_user_id > 0 {
        telegram_user_id
    } else {
        crate::db::telegram::get_user_telegram_id(&ctx.state.pool_ro, mediafusion_user_id)
            .await
            .unwrap_or(0)
    };
    let admin_summary = format!(
        "📡 *Telegram Scrape Completed*\n\n\
         User: `{notify_user_id}` (MediaFusion #{mf_id})\n\
         Depth: {limit_label}\n\n{summary_body}",
        mf_id = mediafusion_user_id.0,
        summary_body = scrape_summary_body(channel_results, totals)
    );
    let admin_chat_id = notification_chat_id
        .parse::<i64>()
        .unwrap_or(chat_id.unwrap_or(0));
    match api
        .send_message(admin_chat_id, &admin_summary, None)
        .await
    {
        Ok(_) => info!(
            "telegram_bg: sent admin scrape summary to chat {admin_chat_id} for user {}",
            mediafusion_user_id.0
        ),
        Err(e) => warn!(
            "telegram_bg: failed to send admin scrape summary to chat {admin_chat_id}: {e}"
        ),
    }
}

fn build_scrape_summary(
    channel_results: &[ChannelScrapeResult],
    totals: &ScrapeMetrics,
    limit_label: &str,
    telegram_user_id: i64,
    mediafusion_user_id: UserId,
) -> String {
    let _ = (telegram_user_id, mediafusion_user_id);
    let body = scrape_summary_body(channel_results, totals);
    if channel_results.len() == 1 {
        format!(
            "✅ *Scrape Complete*\n\nChannel: `{}`\nDepth: {limit_label}\n\n{body}",
            channel_results[0].channel
        )
    } else {
        format!(
            "✅ *Scrape Complete*\n\nChannels scraped: {}\nDepth: {limit_label}\n\n{body}",
            channel_results.len()
        )
    }
}

fn scrape_summary_body(channel_results: &[ChannelScrapeResult], totals: &ScrapeMetrics) -> String {
    let mut lines = vec![format!(
        "📊 *Total results:*\n• Imported: {}\n• Skipped: {}\n• Errors: {}",
        totals.imported, totals.skipped, totals.errors
    )];

    if channel_results.len() > 1 {
        lines.push("\n*Per channel:*".to_string());
        for result in channel_results {
            lines.push(format!(
                "• `{}`: {} imported, {} skipped, {} errors",
                result.channel,
                result.metrics.imported,
                result.metrics.skipped,
                result.metrics.errors
            ));
        }
    }

    lines.join("\n")
}

async fn scrape_and_persist_channel(
    ctx: &JobCtx,
    client: &grammers_client::Client,
    channel: &str,
    message_limit: Option<i32>,
    min_size: u64,
) -> ScrapeMetrics {
    info!(
        "telegram_bg: scraping channel {channel} (limit={})",
        format_scrape_message_limit(message_limit)
    );

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
    let dialog_peers = crate::services::telegram_peer::load_dialog_peer_map(client).await;
    let streams = telegram::scrape(
        client,
        &[channel.to_string()],
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

        if telegram_stream_already_indexed(&ctx.state.pool, stream).await {
            metrics.skipped += 1;
            continue;
        }

        let (season, episode, episode_end) = telegram::series_episode_from_parsed(&stream.parsed);
        let is_series = season.is_some() || episode.is_some();
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

        let Some(meta) = meta_result else {
            metrics.skipped += 1;
            continue;
        };

        let enrichment = crate::services::telegram_backup::enrich_scraped_stream(
            &ctx.state,
            client,
            channel,
            stream,
            &dialog_peers,
            title,
        )
        .await;

        let mut input: crate::db::TelegramStoreInput = stream.into();
        input.backup_chat_id = enrichment.backup_chat_id;
        input.backup_message_id = enrichment.backup_message_id;
        input.file_id = enrichment.file_id.or(input.file_id);
        input.document_id = enrichment.document_id.or(input.document_id);
        input.file_unique_id = enrichment.file_unique_id.or(input.file_unique_id);

        let mut opts = stream_convert::scraper_store_opts(
            meta.media_id,
            media_type,
            season,
            episode,
        );
        opts.episode_end = episode_end;
        match crate::db::store_telegram_stream(&ctx.state.pool, &input, &opts).await {
            Ok(r) if r.was_inserted() => metrics.imported += 1,
            Ok(_) => metrics.skipped += 1,
            Err(e) => {
                tracing::warn!(
                    "telegram_bg: store failed chat={} msg={}: {e}",
                    input.chat_id,
                    input.message_id
                );
                metrics.errors += 1;
            }
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

async fn telegram_stream_already_indexed(
    pool: &sqlx::PgPool,
    stream: &crate::scrapers::ScrapedTelegramStream,
) -> bool {
    if let Some(ref file_unique_id) = stream.file_unique_id {
        let exists: Option<(i32,)> = sqlx::query_as(
            "SELECT stream_id FROM telegram_stream WHERE file_unique_id = $1 LIMIT 1",
        )
        .bind(file_unique_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);
        if exists.is_some() {
            return true;
        }
    }

    let exists: Option<(i32,)> = sqlx::query_as(
        "SELECT stream_id FROM telegram_stream WHERE chat_id = $1 AND message_id = $2 LIMIT 1",
    )
    .bind(stream.chat_id.to_string())
    .bind(stream.message_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);
    exists.is_some()
}
