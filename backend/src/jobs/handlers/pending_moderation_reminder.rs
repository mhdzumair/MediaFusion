use async_trait::async_trait;
use chrono::{DateTime, Utc};
use tracing::{debug, info, warn};

use crate::jobs::{
    error::JobError,
    handler::{JobCtx, JobHandler},
};

pub struct PendingModerationReminder;

struct QueueCount {
    label: &'static str,
    count: i64,
    oldest: Option<DateTime<Utc>>,
}

#[async_trait]
impl JobHandler for PendingModerationReminder {
    const QUEUE: &'static str = "pending_moderation_reminder";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let config = &ctx.state.config;
        let Some(bot_token) = config.telegram_bot_token.as_deref() else {
            debug!("pending_moderation_reminder: Telegram bot not configured — skipping");
            return Ok(());
        };
        let Some(chat_id) = config.telegram_chat_id.as_deref() else {
            debug!("pending_moderation_reminder: TELEGRAM_CHAT_ID not configured — skipping");
            return Ok(());
        };

        let queues = collect_pending_counts(&ctx.state.pool).await?;
        let total_pending: i64 = queues.iter().map(|q| q.count).sum();
        if total_pending == 0 {
            debug!("pending_moderation_reminder: no pending moderation queues");
            return Ok(());
        }

        let host_url = config.host_url.trim_end_matches('/');
        let mut lines = vec![
            "⏰ Pending Moderation Reminder".to_string(),
            String::new(),
            format!("*Total Pending*: `{total_pending}`"),
            String::new(),
            "*Queues:*".to_string(),
        ];
        for item in &queues {
            if item.count <= 0 {
                continue;
            }
            let oldest_age = format_pending_age(item.oldest);
            lines.push(format!(
                "- *{}*: `{}` pending (oldest `{oldest_age}`)",
                item.label, item.count
            ));
        }
        lines.push(String::new());
        lines.push(format!(
            "*Review Dashboard*: [View]({host_url}/app/dashboard/moderator)"
        ));

        send_telegram_message(&ctx.state.http, bot_token, chat_id, &lines.join("\n")).await;
        info!("pending_moderation_reminder: sent summary for {total_pending} pending item(s)");
        Ok(())
    }
}

async fn collect_pending_counts(pool: &sqlx::PgPool) -> Result<Vec<QueueCount>, JobError> {
    let contribution = pending_count(
        pool,
        "SELECT COUNT(*), MIN(created_at) FROM contributions WHERE status = 'PENDING'",
    )
    .await?;
    let metadata = pending_count(
        pool,
        "SELECT COUNT(*), MIN(created_at) FROM metadata_suggestions WHERE status = 'PENDING'",
    )
    .await?;
    let stream = pending_count(
        pool,
        "SELECT COUNT(*), MIN(created_at) FROM stream_suggestions WHERE status = 'PENDING'",
    )
    .await?;
    let episode = pending_count(
        pool,
        "SELECT COUNT(*), MIN(created_at) FROM episode_suggestions WHERE status = 'PENDING'",
    )
    .await?;
    let annotation = pending_count(
        pool,
        r#"
        WITH unlinked_streams AS (
            SELECT DISTINCT sf.stream_id
            FROM stream_file sf
            INNER JOIN stream s ON s.id = sf.stream_id
            LEFT JOIN file_media_link fml_any ON fml_any.file_id = sf.id
            WHERE s.is_active = true
              AND s.is_blocked = false
              AND s.is_keyword_blocked = false
              AND fml_any.id IS NULL
        ),
        null_episode_pairs AS (
            SELECT DISTINCT sf.stream_id, fml_series.media_id
            FROM stream_file sf
            INNER JOIN stream s ON s.id = sf.stream_id
            INNER JOIN file_media_link fml_series ON fml_series.file_id = sf.id
            INNER JOIN stream_media_link sml
                ON sml.stream_id = sf.stream_id
               AND sml.media_id = fml_series.media_id
            INNER JOIN media m ON m.id = fml_series.media_id
            WHERE s.is_active = true
              AND s.is_blocked = false
              AND s.is_keyword_blocked = false
              AND m.type = 'SERIES'
              AND fml_series.episode_number IS NULL
        ),
        unmapped_pairs AS (
            SELECT DISTINCT us.stream_id, m.id AS media_id
            FROM unlinked_streams us
            INNER JOIN stream_media_link sml ON sml.stream_id = us.stream_id
            INNER JOIN media m ON sml.media_id = m.id
            WHERE m.type = 'SERIES'
            UNION
            SELECT nep.stream_id, nep.media_id
            FROM null_episode_pairs nep
        ),
        annotated_streams AS (
            SELECT DISTINCT s.id AS stream_id, s.created_at
            FROM unmapped_pairs up
            INNER JOIN stream s ON s.id = up.stream_id
        )
        SELECT COUNT(*), MIN(created_at) FROM annotated_streams
        "#,
    )
    .await?;

    Ok(vec![
        QueueCount {
            label: "Content Imports",
            count: contribution.0,
            oldest: contribution.1,
        },
        QueueCount {
            label: "Metadata Suggestions",
            count: metadata.0,
            oldest: metadata.1,
        },
        QueueCount {
            label: "Stream Suggestions",
            count: stream.0,
            oldest: stream.1,
        },
        QueueCount {
            label: "Episode Suggestions",
            count: episode.0,
            oldest: episode.1,
        },
        QueueCount {
            label: "File Annotation Requests",
            count: annotation.0,
            oldest: annotation.1,
        },
    ])
}

async fn pending_count(
    pool: &sqlx::PgPool,
    sql: &str,
) -> Result<(i64, Option<DateTime<Utc>>), JobError> {
    use sqlx::Row;
    let row = sqlx::query(sqlx::AssertSqlSafe(sql))
        .fetch_one(pool)
        .await?;
    let count: i64 = row.try_get(0)?;
    let oldest: Option<DateTime<Utc>> = row.try_get(1)?;
    Ok((count, oldest))
}

fn format_pending_age(oldest: Option<DateTime<Utc>>) -> String {
    let Some(oldest) = oldest else {
        return "unknown".to_string();
    };
    let delta = Utc::now().signed_duration_since(oldest);
    let minutes = delta.num_minutes().max(0);
    let hours = minutes / 60;
    let days = hours / 24;
    if days > 0 {
        format!("{days}d {}h", hours % 24)
    } else if hours > 0 {
        format!("{hours}h {}m", minutes % 60)
    } else {
        format!("{minutes}m")
    }
}

async fn send_telegram_message(http: &reqwest::Client, bot_token: &str, chat_id: &str, text: &str) {
    let url = format!("https://api.telegram.org/bot{bot_token}/sendMessage");
    let payload = serde_json::json!({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": true,
    });
    if let Err(e) = http.post(&url).json(&payload).send().await {
        warn!("pending_moderation_reminder: Telegram send failed: {e}");
    }
}
