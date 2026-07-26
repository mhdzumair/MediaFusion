use async_trait::async_trait;
use chrono::{DateTime, NaiveDate, Utc};
use tracing::{debug, info, warn};

use crate::{
    bot::telegram_moderation::{
        collect_pending_counts, format_pending_queues_section, send_telegram_text,
    },
    db::MediaType,
    db::StreamType,
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
};

pub struct DailyDigest;

struct PeriodStats {
    new_streams: i64,
    streams_by_type: Vec<(StreamType, i64)>,
    new_media: i64,
    media_by_type: Vec<(MediaType, i64)>,
    new_users: i64,
    active_users: i64,
    playbacks: i64,
    contributions_submitted: i64,
    contributions_approved: i64,
    contributions_rejected: i64,
    stream_suggestions_submitted: i64,
    stream_suggestions_approved: i64,
    stream_suggestions_rejected: i64,
    metadata_suggestions_submitted: i64,
    metadata_suggestions_approved: i64,
    metadata_suggestions_rejected: i64,
    episode_suggestions_submitted: i64,
    episode_suggestions_approved: i64,
    episode_suggestions_rejected: i64,
}

#[async_trait]
impl JobHandler for DailyDigest {
    const QUEUE: &'static str = "daily_digest";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let config = &ctx.state.config;
        let Some(bot_token) = config.telegram_bot_token.as_deref() else {
            debug!("daily_digest: Telegram bot not configured — skipping");
            return Ok(());
        };
        let Some(chat_id) = config.telegram_chat_id.as_deref() else {
            debug!("daily_digest: TELEGRAM_CHAT_ID not configured — skipping");
            return Ok(());
        };

        let period_end = Utc::now();
        let period_start = period_end - chrono::Duration::hours(24);
        let stat_date = period_end.date_naive();

        let stats = collect_period_stats(&ctx.state.pool, period_start, period_end).await?;
        let queues = collect_pending_counts(&ctx.state.pool).await?;

        let host_url = config.host_url.trim_end_matches('/');
        let message = build_digest_message(stat_date, &stats, &queues, host_url);

        send_telegram_text(&ctx.state.http, bot_token, chat_id, &message).await;
        store_daily_stats(&ctx.state.pool, stat_date, &stats).await;

        info!(
            "daily_digest: sent digest ({} new streams, {} new media, {} playbacks)",
            stats.new_streams, stats.new_media, stats.playbacks
        );
        Ok(())
    }
}

async fn collect_period_stats(
    pool: &sqlx::PgPool,
    period_start: DateTime<Utc>,
    period_end: DateTime<Utc>,
) -> Result<PeriodStats, JobError> {
    let new_streams = count_since(
        pool,
        "SELECT COUNT(*) FROM stream WHERE created_at >= $1 AND created_at < $2",
        period_start,
        period_end,
    )
    .await?;

    let streams_by_type = sqlx::query_as::<_, (StreamType, i64)>(
        "SELECT stream_type, COUNT(*) FROM stream \
         WHERE created_at >= $1 AND created_at < $2 GROUP BY stream_type ORDER BY COUNT(*) DESC",
    )
    .bind(period_start)
    .bind(period_end)
    .fetch_all(pool)
    .await?;

    let new_media = count_since(
        pool,
        "SELECT COUNT(*) FROM media WHERE created_at >= $1 AND created_at < $2",
        period_start,
        period_end,
    )
    .await?;

    let media_by_type = sqlx::query_as::<_, (MediaType, i64)>(
        "SELECT type, COUNT(*) FROM media \
         WHERE created_at >= $1 AND created_at < $2 GROUP BY type ORDER BY COUNT(*) DESC",
    )
    .bind(period_start)
    .bind(period_end)
    .fetch_all(pool)
    .await?;

    let new_users = count_since(
        pool,
        "SELECT COUNT(*) FROM users WHERE created_at >= $1 AND created_at < $2",
        period_start,
        period_end,
    )
    .await?;

    let active_users = count_since(
        pool,
        "SELECT COUNT(*) FROM users WHERE last_login >= $1 AND last_login < $2",
        period_start,
        period_end,
    )
    .await?;

    let playbacks = count_since(
        pool,
        "SELECT COUNT(*) FROM watch_history WHERE watched_at >= $1 AND watched_at < $2",
        period_start,
        period_end,
    )
    .await?;

    let contributions_submitted = count_since(
        pool,
        "SELECT COUNT(*) FROM contributions WHERE created_at >= $1 AND created_at < $2",
        period_start,
        period_end,
    )
    .await?;
    let contributions_approved = count_since(
        pool,
        "SELECT COUNT(*) FROM contributions WHERE status = 'APPROVED' \
         AND reviewed_at >= $1 AND reviewed_at < $2",
        period_start,
        period_end,
    )
    .await?;
    let contributions_rejected = count_since(
        pool,
        "SELECT COUNT(*) FROM contributions WHERE status = 'REJECTED' \
         AND reviewed_at >= $1 AND reviewed_at < $2",
        period_start,
        period_end,
    )
    .await?;

    let stream_suggestions_submitted = count_since(
        pool,
        "SELECT COUNT(*) FROM stream_suggestions WHERE created_at >= $1 AND created_at < $2",
        period_start,
        period_end,
    )
    .await?;
    let stream_suggestions_approved = count_since(
        pool,
        "SELECT COUNT(*) FROM stream_suggestions \
         WHERE status IN ('approved', 'auto_approved') AND reviewed_at >= $1 AND reviewed_at < $2",
        period_start,
        period_end,
    )
    .await?;
    let stream_suggestions_rejected = count_since(
        pool,
        "SELECT COUNT(*) FROM stream_suggestions \
         WHERE status = 'rejected' AND reviewed_at >= $1 AND reviewed_at < $2",
        period_start,
        period_end,
    )
    .await?;

    let metadata_suggestions_submitted = count_since(
        pool,
        "SELECT COUNT(*) FROM metadata_suggestions WHERE created_at >= $1 AND created_at < $2",
        period_start,
        period_end,
    )
    .await?;
    let metadata_suggestions_approved = count_since(
        pool,
        "SELECT COUNT(*) FROM metadata_suggestions \
         WHERE status IN ('approved', 'auto_approved') AND reviewed_at >= $1 AND reviewed_at < $2",
        period_start,
        period_end,
    )
    .await?;
    let metadata_suggestions_rejected = count_since(
        pool,
        "SELECT COUNT(*) FROM metadata_suggestions \
         WHERE status = 'rejected' AND reviewed_at >= $1 AND reviewed_at < $2",
        period_start,
        period_end,
    )
    .await?;

    let episode_suggestions_submitted = count_since(
        pool,
        "SELECT COUNT(*) FROM episode_suggestions WHERE created_at >= $1 AND created_at < $2",
        period_start,
        period_end,
    )
    .await?;
    let episode_suggestions_approved = count_since(
        pool,
        "SELECT COUNT(*) FROM episode_suggestions \
         WHERE status IN ('approved', 'auto_approved') AND reviewed_at >= $1 AND reviewed_at < $2",
        period_start,
        period_end,
    )
    .await?;
    let episode_suggestions_rejected = count_since(
        pool,
        "SELECT COUNT(*) FROM episode_suggestions \
         WHERE status = 'rejected' AND reviewed_at >= $1 AND reviewed_at < $2",
        period_start,
        period_end,
    )
    .await?;

    Ok(PeriodStats {
        new_streams,
        streams_by_type,
        new_media,
        media_by_type,
        new_users,
        active_users,
        playbacks,
        contributions_submitted,
        contributions_approved,
        contributions_rejected,
        stream_suggestions_submitted,
        stream_suggestions_approved,
        stream_suggestions_rejected,
        metadata_suggestions_submitted,
        metadata_suggestions_approved,
        metadata_suggestions_rejected,
        episode_suggestions_submitted,
        episode_suggestions_approved,
        episode_suggestions_rejected,
    })
}

async fn count_since(
    pool: &sqlx::PgPool,
    sql: &str,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
) -> Result<i64, JobError> {
    sqlx::query_scalar(sqlx::AssertSqlSafe(sql))
        .bind(start)
        .bind(end)
        .fetch_one(pool)
        .await
        .map_err(JobError::from)
}

fn build_digest_message(
    stat_date: NaiveDate,
    stats: &PeriodStats,
    queues: &[crate::bot::telegram_moderation::QueueCount],
    host_url: &str,
) -> String {
    let mut lines = vec![
        "📊 *MediaFusion Daily Digest*".to_string(),
        format!("📅 {stat_date}"),
        String::new(),
        "*New Streams (24h)*".to_string(),
        format!("- Total: `{}`", stats.new_streams),
    ];

    if stats.streams_by_type.is_empty() {
        lines.push("  • No new streams".to_string());
    } else {
        for (stream_type, count) in &stats.streams_by_type {
            lines.push(format!(
                "  • {}: `{count}`",
                stream_type.as_wire().to_ascii_lowercase()
            ));
        }
    }

    lines.push(String::new());
    lines.push("*New Media (24h)*".to_string());
    lines.push(format!("- Total: `{}`", stats.new_media));

    if stats.media_by_type.is_empty() {
        lines.push("  • No new media".to_string());
    } else {
        for (media_type, count) in &stats.media_by_type {
            lines.push(format!("  • {}: `{count}`", media_type.as_wire()));
        }
    }

    lines.extend([
        String::new(),
        "*Users (24h)*".to_string(),
        format!("- New: `{}`", stats.new_users),
        format!("- Active: `{}`", stats.active_users),
        format!("- Playbacks: `{}`", stats.playbacks),
        String::new(),
        "*Contributions (24h)*".to_string(),
        format!("- Submitted: `{}`", stats.contributions_submitted),
        format!("- Approved: `{}`", stats.contributions_approved),
        format!("- Rejected: `{}`", stats.contributions_rejected),
        String::new(),
        "*Suggestions (24h)*".to_string(),
        format!(
            "- Stream: `{}` submitted, `{}` approved, `{}` rejected",
            stats.stream_suggestions_submitted,
            stats.stream_suggestions_approved,
            stats.stream_suggestions_rejected
        ),
        format!(
            "- Metadata: `{}` submitted, `{}` approved, `{}` rejected",
            stats.metadata_suggestions_submitted,
            stats.metadata_suggestions_approved,
            stats.metadata_suggestions_rejected
        ),
        format!(
            "- Episode: `{}` submitted, `{}` approved, `{}` rejected",
            stats.episode_suggestions_submitted,
            stats.episode_suggestions_approved,
            stats.episode_suggestions_rejected
        ),
    ]);

    let queue_lines = format_pending_queues_section(queues);
    if !queue_lines.is_empty() {
        lines.push(String::new());
        lines.extend(queue_lines);
    }

    lines.push(String::new());
    lines.push(format!(
        "*Moderator Dashboard*: [View]({host_url}/app/dashboard/moderator)"
    ));
    lines.push(format!(
        "*Annotation Queue*: [View]({host_url}/app/dashboard/moderator?tab=annotation)"
    ));

    lines.join("\n")
}

async fn store_daily_stats(pool: &sqlx::PgPool, stat_date: NaiveDate, stats: &PeriodStats) {
    let result = sqlx::query(
        r#"INSERT INTO daily_stats
           (stat_date, new_users, active_users, new_streams, total_playbacks, created_at)
           VALUES ($1, $2, $3, $4, $5, NOW())
           ON CONFLICT (stat_date) DO UPDATE SET
             new_users = EXCLUDED.new_users,
             active_users = EXCLUDED.active_users,
             new_streams = EXCLUDED.new_streams,
             total_playbacks = EXCLUDED.total_playbacks"#,
    )
    .bind(stat_date)
    .bind(stats.new_users as i32)
    .bind(stats.active_users as i32)
    .bind(stats.new_streams as i32)
    .bind(stats.playbacks as i32)
    .execute(pool)
    .await;

    if let Err(e) = result {
        warn!("daily_digest: failed to store daily_stats for {stat_date}: {e}");
    }
}
