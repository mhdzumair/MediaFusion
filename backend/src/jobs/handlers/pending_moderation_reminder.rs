use async_trait::async_trait;
use tracing::{debug, info};

use crate::{
    bot::telegram_moderation::{collect_pending_counts, send_telegram_text},
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
};

pub struct PendingModerationReminder;

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
            let oldest_age = crate::bot::telegram_moderation::format_pending_age(item.oldest);
            lines.push(format!(
                "- *{}*: `{}` pending (oldest `{oldest_age}`)",
                item.label, item.count
            ));
        }
        lines.push(String::new());
        lines.push(format!(
            "*Review Dashboard*: [View]({host_url}/app/dashboard/moderator)"
        ));

        send_telegram_text(&ctx.state.http, bot_token, chat_id, &lines.join("\n")).await;
        info!("pending_moderation_reminder: sent summary for {total_pending} pending item(s)");
        Ok(())
    }
}
