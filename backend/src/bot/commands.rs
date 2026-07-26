//! Bot command handlers.

use crate::{db::telegram as tg_db, state::AppState};

use super::{
    api::BotApi, batch, channels, detect, dialog_picker, disabled_content, login, session_setup,
    state_store, text, wizard,
};
use crate::scrapers::telegram::{
    self, DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT, format_scrape_message_limit,
};

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
        "/session" => {
            session_setup::handle_session_command(state, api, user_id, chat_id).await;
        }
        "/dropsession" => {
            session_setup::handle_drop_session_command(state, api, user_id, chat_id).await;
        }
        "/addchannel" => {
            channels::handle_add_channel_command(state, api, user_id, chat_id, text_msg).await;
        }
        "/removechannel" => {
            channels::handle_remove_channel_command(state, api, user_id, chat_id, text_msg).await;
        }
        "/channels" => {
            channels::handle_list_channels_command(state, api, user_id, chat_id).await;
        }
        "/browsechannels" => {
            dialog_picker::handle_browse_channels_command(state, api, user_id, chat_id).await;
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
        let session_connected =
            crate::db::user_telegram_session::has_session(&state.pool_ro, mf_id).await;
        let channel_count = crate::db::telegram_channels::list_user_channels(&state.pool_ro, mf_id)
            .await
            .len();
        text::status_linked(
            &username,
            i64::from(i32::from(mf_id)),
            session_connected,
            channel_count,
        )
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

    if state_store::get_scrape_setup(state, user_id)
        .await
        .is_some()
    {
        state_store::clear_scrape_setup(state, user_id).await;
        let _ = api
            .send_message(
                chat_id,
                "❌ *Scrape Cancelled*\n\nMessage count prompt cancelled.",
                None,
            )
            .await;
        return;
    }

    if session_setup::clear_if_active(state, user_id).await {
        return;
    }

    if channels::clear_if_active(state, user_id).await {
        let _ = api
            .send_message(chat_id, "❌ *Channel Setup Cancelled*", None)
            .await;
        return;
    }

    let _ = api
        .send_message(chat_id, &text::cancel_nothing(), None)
        .await;
}

async fn handle_scrape(state: &AppState, api: &BotApi, user_id: i64, chat_id: i64, text_msg: &str) {
    let Some((mediafusion_user_id, _)) =
        tg_db::get_user_by_telegram_id(&state.pool_ro, user_id).await
    else {
        let _ = api
            .send_message(
                chat_id,
                "🔐 *Account Required*\n\nLink your MediaFusion account first.\n\nSend `/login` to get started.",
                None,
            )
            .await;
        return;
    };

    if !crate::db::user_telegram_session::has_session(&state.pool_ro, mediafusion_user_id).await {
        let _ = api
            .send_message(
                chat_id,
                "🔐 *Telegram Session Required*\n\n\
                 Connect your Telegram account with `/session` before scraping channels.",
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

    let (message_limit, channel, scrape_all) = match parse_scrape_command(text_msg) {
        Ok(parsed) => parsed,
        Err(message) => {
            let _ = api.send_message(chat_id, &message, None).await;
            return;
        }
    };

    if scrape_all {
        let channels = crate::db::telegram_channels::user_scraping_channels(
            &state.pool_ro,
            mediafusion_user_id,
        )
        .await;
        if channels.is_empty() {
            let _ = api
                .send_message(
                    chat_id,
                    "ℹ️ *No Channels*\n\n\
                     Add scraping channels first with `/addchannel`, then run `/scrape`.",
                    None,
                )
                .await;
            return;
        }
    }

    let Some(message_limit) = message_limit else {
        state_store::save_scrape_setup(
            state,
            user_id,
            &state_store::ScrapeSetupState {
                mediafusion_user_id: mediafusion_user_id.0,
                chat_id,
                channel: channel.clone(),
                scrape_all,
            },
        )
        .await;

        let target = channel
            .as_deref()
            .map(|c| format!("Channel: `{c}`"))
            .unwrap_or_else(|| "All configured channels".to_string());
        let _ = api
            .send_message(
                chat_id,
                &format!(
                    "📨 *Scrape Depth*\n\n\
                     Target: {target}\n\n\
                     How many recent messages should be scanned per channel?\n\n\
                     • Reply with a number (default `{DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT}`)\n\
                     • Reply `all` to scan the full channel history\n\n\
                     Send `/cancel` to abort."
                ),
                None,
            )
            .await;
        return;
    };

    start_scrape_job(
        state,
        api,
        user_id,
        chat_id,
        mediafusion_user_id,
        channel,
        scrape_all,
        message_limit,
    )
    .await;
}

pub async fn handle_scrape_limit_input(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    text_input: &str,
) -> bool {
    let Some(setup) = state_store::get_scrape_setup(state, user_id).await else {
        return false;
    };

    let message_limit = match telegram::parse_scrape_message_limit(text_input) {
        Ok(limit) => limit,
        Err(_) => {
            let _ = api
                .send_message(
                    chat_id,
                    &format!(
                        "❌ *Invalid Count*\n\n\
                         Reply with a positive number, `all`, or press Enter for the default `{DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT}`."
                    ),
                    None,
                )
                .await;
            return true;
        }
    };

    state_store::clear_scrape_setup(state, user_id).await;
    start_scrape_job(
        state,
        api,
        user_id,
        chat_id,
        crate::db::types::UserId(setup.mediafusion_user_id),
        setup.channel,
        setup.scrape_all,
        message_limit,
    )
    .await;
    true
}

type ScrapeCommandParse = Result<(Option<Option<i32>>, Option<String>, bool), String>;

fn parse_scrape_command(text_msg: &str) -> ScrapeCommandParse {
    let parts: Vec<&str> = text_msg.split_whitespace().collect();
    let mut message_limit: Option<Option<i32>> = None;
    let mut channel: Option<String> = None;

    for part in parts.iter().skip(1) {
        if let Ok(limit) = telegram::parse_scrape_message_limit(part) {
            message_limit = Some(limit);
        } else if let Some(normalized) = detect::normalize_channel_identifier(part) {
            channel = Some(normalized);
        } else {
            return Err(format!(
                "⚠️ *Invalid Scrape Command*\n\n\
                 Could not understand `{part}`.\n\n\
                 *Examples:*\n\
                 `/scrape` — choose depth, scrape all channels\n\
                 `/scrape 50` — last 50 messages per channel\n\
                 `/scrape all @channel` — full history for one channel\n\
                 `/scrape 25 @channel` — last 25 messages for one channel"
            ));
        }
    }

    let scrape_all = channel.is_none();
    Ok((message_limit, channel, scrape_all))
}

async fn start_scrape_job(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    mediafusion_user_id: crate::db::types::UserId,
    channel: Option<String>,
    scrape_all: bool,
    message_limit: Option<i32>,
) {
    let progress_label = channel
        .as_deref()
        .map(|c| format!("Channel: `{c}`"))
        .unwrap_or_else(|| "All configured channels".to_string());
    let depth_label = format_scrape_message_limit(message_limit);

    let progress_id = match api
        .send_message(
            chat_id,
            &format!(
                "🔍 *Starting Scrape*\n\n{progress_label}\nDepth: {depth_label}\n\n⏳ Initializing..."
            ),
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
            "scrape_all": scrape_all,
            "progress_message_id": progress_id,
            "chat_id": chat_id,
            "message_limit": message_limit,
        })
        .to_string(),
    )
    .await;

    let mut payload = serde_json::json!({
        "mediafusion_user_id": mediafusion_user_id.0,
        "telegram_user_id": user_id,
        "chat_id": chat_id,
        "progress_message_id": progress_id,
        "scrape_all": scrape_all,
        "scrape_all_messages": message_limit.is_none(),
    });
    if let Some(limit) = message_limit {
        payload["message_limit"] = serde_json::json!(limit);
    }
    if let Some(channel) = channel {
        payload["channel"] = serde_json::json!(channel);
    }

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
