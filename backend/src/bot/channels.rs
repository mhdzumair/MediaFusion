//! Bot-driven Telegram scraping channel management (per-user profile channels).

use crate::{
    db::{
        self,
        telegram_channels::{self, ChannelMutationError},
        types::UserId,
    },
    state::AppState,
};

use super::{api::BotApi, detect, state_store};

pub async fn handle_add_channel_command(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
    text_msg: &str,
) {
    let Some(mediafusion_user_id) =
        require_linked_account(state, api, telegram_user_id, chat_id).await
    else {
        return;
    };

    let parts: Vec<&str> = text_msg.split_whitespace().collect();
    let raw_channel = parts.get(1).copied().unwrap_or("");

    if raw_channel.is_empty() {
        state_store::save_channel_setup(
            state,
            telegram_user_id,
            &state_store::ChannelSetupState {
                action: state_store::ChannelSetupAction::Add,
            },
        )
        .await;
        let _ = api
            .send_message(
                chat_id,
                "➕ *Add Scraping Channel*\n\n\
                 Reply with a channel @username, t.me link, or stored id (id:-100…).\n\n\
                 Or send /browsechannels to pick public or private channels from your Telegram account.\n\n\
                 Send /cancel to abort.",
                None,
            )
            .await;
        return;
    }

    add_channel(
        state,
        api,
        telegram_user_id,
        chat_id,
        mediafusion_user_id,
        raw_channel,
    )
    .await;
}

pub async fn handle_remove_channel_command(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
    text_msg: &str,
) {
    let Some(mediafusion_user_id) =
        require_linked_account(state, api, telegram_user_id, chat_id).await
    else {
        return;
    };

    let parts: Vec<&str> = text_msg.split_whitespace().collect();
    let raw_channel = parts.get(1).copied().unwrap_or("");

    if raw_channel.is_empty() {
        let channels =
            telegram_channels::list_user_channels(&state.pool_ro, mediafusion_user_id).await;
        if channels.is_empty() {
            let _ = api
                .send_message(
                    chat_id,
                    "ℹ️ *No Channels*\n\nYou have no scraping channels configured.\n\nUse /addchannel or /browsechannels.",
                    None,
                )
                .await;
            return;
        }

        state_store::save_channel_setup(
            state,
            telegram_user_id,
            &state_store::ChannelSetupState {
                action: state_store::ChannelSetupAction::Remove,
            },
        )
        .await;

        let list = channels
            .iter()
            .map(|ch| format!("• {} — {}", ch.id, ch.name))
            .collect::<Vec<_>>()
            .join("\n");

        let _ = api
            .send_message(
                chat_id,
                &format!(
                    "➖ *Remove Scraping Channel*\n\n\
                     Reply with the channel to remove:\n\n\
                     {list}\n\n\
                     Send /cancel to abort."
                ),
                None,
            )
            .await;
        return;
    }

    remove_channel(state, api, chat_id, mediafusion_user_id, raw_channel).await;
}

pub async fn handle_list_channels_command(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
) {
    let Some(mediafusion_user_id) =
        require_linked_account(state, api, telegram_user_id, chat_id).await
    else {
        return;
    };

    let channels = telegram_channels::list_user_channels(&state.pool_ro, mediafusion_user_id).await;
    if channels.is_empty() {
        let _ = api
            .send_message(
                chat_id,
                "ℹ️ *No Channels*\n\nYou have no scraping channels configured.\n\nUse /addchannel or /browsechannels.",
                None,
            )
            .await;
        return;
    }

    let list = channels
        .iter()
        .map(|ch| {
            let status = if ch.enabled { "enabled" } else { "disabled" };
            format!("• {} — {} ({status})", ch.id, ch.name)
        })
        .collect::<Vec<_>>()
        .join("\n");

    let _ = api
        .send_message(
            chat_id,
            &format!(
                "📋 *Your Scraping Channels*\n\n\
                 {list}\n\n\
                 /addchannel — add by @username\n\
                 /browsechannels — pick from Telegram\n\
                 /removechannel — remove a channel\n\
                 /scrape — scrape all\n\
                 /scrape @channel — scrape one"
            ),
            None,
        )
        .await;
}

/// Returns true when the message was consumed by channel setup flow.
pub async fn handle_text(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
    text_msg: &str,
) -> bool {
    let Some(setup) = state_store::get_channel_setup(state, telegram_user_id).await else {
        return false;
    };

    let Some(mediafusion_user_id) =
        db::telegram::get_user_by_telegram_id(&state.pool_ro, telegram_user_id)
            .await
            .map(|(id, _)| id)
    else {
        state_store::clear_channel_setup(state, telegram_user_id).await;
        return true;
    };

    match setup.action {
        state_store::ChannelSetupAction::Add => {
            add_channel(
                state,
                api,
                telegram_user_id,
                chat_id,
                mediafusion_user_id,
                text_msg,
            )
            .await;
        }
        state_store::ChannelSetupAction::Remove => {
            remove_channel(state, api, chat_id, mediafusion_user_id, text_msg).await;
        }
    }

    state_store::clear_channel_setup(state, telegram_user_id).await;
    true
}

async fn add_channel(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
    mediafusion_user_id: UserId,
    raw_channel: &str,
) {
    let channel = match detect::normalize_channel_identifier(raw_channel) {
        Some(c) => c,
        None => {
            let _ = api
                .send_message(
                    chat_id,
                    "⚠️ *Invalid Channel*\n\n\
                     Provide a channel @username or t.me link.\n\n\
                     Examples: @channelname or https://t.me/channelname",
                    None,
                )
                .await;
            return;
        }
    };

    match telegram_channels::add_user_channel(&state.pool, mediafusion_user_id, &channel, None)
        .await
    {
        Ok(ch) => {
            state_store::clear_channel_setup(state, telegram_user_id).await;
            let _ = api
                .send_message(
                    chat_id,
                    &format!(
                        "✅ *Channel Added*\n\n\
                         {} is saved for scraping.\n\n\
                         Run /scrape {} now or /scrape for all channels.",
                        ch.id, ch.id
                    ),
                    None,
                )
                .await;
        }
        Err(ChannelMutationError::Duplicate) => {
            let _ = api
                .send_message(
                    chat_id,
                    &format!("ℹ️ Channel {channel} is already in your list."),
                    None,
                )
                .await;
        }
        Err(ChannelMutationError::NotFound) => {
            let _ = api
                .send_message(chat_id, "⚠️ Invalid channel identifier.", None)
                .await;
        }
        Err(ChannelMutationError::Database(e)) => {
            tracing::error!("add channel: {e}");
            let _ = api
                .send_message(chat_id, "❌ Failed to save channel.", None)
                .await;
        }
    }
}

async fn remove_channel(
    state: &AppState,
    api: &BotApi,
    chat_id: i64,
    mediafusion_user_id: UserId,
    raw_channel: &str,
) {
    let channel = match detect::normalize_channel_identifier(raw_channel) {
        Some(c) => c,
        None => {
            let _ = api
                .send_message(
                    chat_id,
                    "⚠️ *Invalid Channel*\n\nProvide a channel @username or t.me link.",
                    None,
                )
                .await;
            return;
        }
    };

    match telegram_channels::remove_user_channel(&state.pool, mediafusion_user_id, &channel).await {
        Ok(true) => {
            let _ = api
                .send_message(
                    chat_id,
                    &format!("✅ Removed {channel} from your scraping channels."),
                    None,
                )
                .await;
        }
        Ok(false) => {
            let _ = api
                .send_message(
                    chat_id,
                    &format!("ℹ️ Channel {channel} was not in your list."),
                    None,
                )
                .await;
        }
        Err(e) => {
            tracing::error!("remove channel: {e}");
            let _ = api
                .send_message(chat_id, "❌ Failed to remove channel.", None)
                .await;
        }
    }
}

async fn require_linked_account(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
) -> Option<UserId> {
    match db::telegram::get_user_by_telegram_id(&state.pool_ro, telegram_user_id).await {
        Some((mediafusion_user_id, _)) => Some(mediafusion_user_id),
        None => {
            let _ = api
                .send_message(
                    chat_id,
                    "🔐 *Account Required*\n\nLink your MediaFusion account first.\n\nSend /login to get started.",
                    None,
                )
                .await;
            None
        }
    }
}

pub async fn clear_if_active(state: &AppState, telegram_user_id: i64) -> bool {
    if state_store::get_channel_setup(state, telegram_user_id)
        .await
        .is_none()
    {
        return false;
    }
    state_store::clear_channel_setup(state, telegram_user_id).await;
    true
}
