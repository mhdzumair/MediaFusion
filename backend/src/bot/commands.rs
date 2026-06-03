//! Bot command handlers.

use crate::{db::telegram as tg_db, state::AppState};

use super::{api::BotApi, batch, detect, disabled_content, login, state_store, text, wizard};

pub async fn handle_command(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    text_msg: &str,
) {
    let cmd = text_msg.split_whitespace().next().unwrap_or("");
    let cmd = cmd.split('@').next().unwrap_or(cmd);

    match cmd {
        "/start" => {
            let name = None::<&str>;
            let enabled =
                disabled_content::enabled_content_lines(&state.config.disabled_content_types);
            let _ = api
                .send_message(chat_id, &text::welcome(name, &enabled), None)
                .await;
        }
        "/help" => {
            let enabled =
                disabled_content::enabled_content_lines(&state.config.disabled_content_types);
            let _ = api
                .send_message(chat_id, &text::help_text(&enabled), None)
                .await;
        }
        "/login" => {
            let result = login::handle_login_command(state, api, user_id, chat_id).await;
            if !result.success {
                tracing::warn!("telegram /login failed for user {user_id}");
            }
            let _ = api.send_message(chat_id, &result.message, None).await;
        }
        "/status" => handle_status(state, api, user_id, chat_id).await,
        "/cancel" => handle_cancel(state, api, user_id, chat_id).await,
        "/scrape" => handle_scrape(state, api, user_id, chat_id, text_msg).await,
        _ => {}
    }
}

async fn handle_status(state: &AppState, api: &BotApi, telegram_user_id: i64, chat_id: i64) {
    let msg = if let Some((mf_id, username)) =
        tg_db::get_user_by_telegram_id(&state.pool_ro, telegram_user_id).await
    {
        text::status_linked(&username, i64::from(i32::from(mf_id)))
    } else {
        text::status_not_linked()
    };
    let _ = api.send_message(chat_id, &msg, None).await;
}

async fn handle_cancel(state: &AppState, api: &BotApi, user_id: i64, chat_id: i64) {
    if let Some(conv) = state_store::get_conversation(state, user_id).await {
        let batch_item_id = conv.batch_item_id.clone();
        state_store::clear_conversation(state, user_id).await;
        if let Some(item_id) = batch_item_id {
            batch::finish_item_review(state, api, user_id, &item_id, false).await;
            let _ = api
                .send_message(chat_id, "↩️ *Returned to batch.*", None)
                .await;
            return;
        }
        let _ = api
            .send_message(chat_id, &text::cancel_success(), None)
            .await;
        return;
    }

    if state_store::get_batch(state, user_id).await.is_some() {
        state_store::clear_batch(state, user_id).await;
        let _ = api.send_message(chat_id, &text::cancel_batch(), None).await;
        return;
    }

    if state_store::scrape_job_exists(state, user_id).await {
        state_store::clear_scrape_job(state, user_id).await;
        let _ = api
            .send_message(
                chat_id,
                "❌ *Scrape Cancelled*\n\nYour scraping job was cancelled.",
                None,
            )
            .await;
        return;
    }

    let _ = api
        .send_message(chat_id, &text::cancel_nothing(), None)
        .await;
}

async fn handle_scrape(state: &AppState, api: &BotApi, user_id: i64, chat_id: i64, text_msg: &str) {
    let parts: Vec<&str> = text_msg.split_whitespace().collect();
    let raw_channel = parts.get(1).copied().unwrap_or("");
    let channel = match detect::normalize_channel_identifier(raw_channel) {
        Some(c) => c,
        None => {
            let _ = api
                .send_message(
                    chat_id,
                    "⚠️ *Invalid Channel*\n\n\
                     Please provide a public channel username or link.\n\n\
                     *Examples:*\n\
                     `/scrape @channelname`\n\
                     `/scrape https://t.me/channelname`",
                    None,
                )
                .await;
            return;
        }
    };

    if tg_db::get_user_by_telegram_id(&state.pool_ro, user_id)
        .await
        .is_none()
    {
        let _ = api
            .send_message(
                chat_id,
                "🔐 *Account Required*\n\nLink your MediaFusion account first.\n\nSend `/login` to get started.",
                None,
            )
            .await;
        return;
    }

    if state_store::scrape_job_exists(state, user_id).await {
        let _ = api
            .send_message(
                chat_id,
                "⏳ *Scrape In Progress*\n\n\
                 You already have an active scraping job. \
                 Wait for it to finish or send `/cancel` to abort it.",
                None,
            )
            .await;
        return;
    }

    let progress_id = match api
        .send_message(
            chat_id,
            &format!("🔍 *Starting Scrape*\n\nChannel: `{channel}`\n\n⏳ Initializing..."),
            None,
        )
        .await
    {
        Ok(id) => id,
        Err(e) => {
            tracing::warn!("scrape progress message: {e}");
            return;
        }
    };

    state_store::set_scrape_job(
        state,
        user_id,
        &serde_json::json!({
            "channel": channel,
            "progress_message_id": progress_id,
            "chat_id": chat_id,
        })
        .to_string(),
    )
    .await;

    let payload = serde_json::json!({
        "user_id": user_id,
        "telegram_user_id": user_id,
        "channel": channel,
        "chat_id": chat_id,
        "progress_message_id": progress_id,
    });

    if let Err(e) = crate::jobs::enqueue_simple(
        &state.pool,
        "telegram_bg",
        &payload,
        crate::jobs::EnqueueOpts {
            dedupe_key: Some(format!("telegram_scrape_user:{user_id}")),
            ..Default::default()
        },
    )
    .await
    {
        tracing::error!("enqueue scrape job: {e}");
        state_store::clear_scrape_job(state, user_id).await;
        let _ = api
            .send_message(chat_id, "❌ Failed to start scrape job.", None)
            .await;
    }
}

pub async fn handle_content_message(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
    content_type: super::model::ContentType,
    raw_input: serde_json::Value,
) {
    if tg_db::get_user_by_telegram_id(&state.pool_ro, user_id)
        .await
        .is_none()
    {
        let _ = api.send_message(
            chat_id,
            "🔐 *Account Required*\n\nLink your MediaFusion account first.\n\nSend `/login` to get started.",
            None,
        ).await;
        return;
    }
    wizard::start_wizard(
        state,
        api,
        user_id,
        chat_id,
        message_id,
        content_type,
        raw_input,
        None,
    )
    .await;
}
