//! Pick channels/groups from the user's connected Telegram session.

use serde_json::json;

use crate::{
    db::{self, telegram_channels},
    services::telegram_dialogs,
    state::AppState,
};

use super::{api::BotApi, callback::CallbackAction, state_store};

const PAGE_SIZE: usize = 8;

pub async fn handle_browse_channels_command(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
) {
    let Some((mediafusion_user_id, _)) =
        db::telegram::get_user_by_telegram_id(&state.pool_ro, telegram_user_id).await
    else {
        let _ = api
            .send_message(
                chat_id,
                "🔐 *Account Required*\n\nLink your MediaFusion account first.\n\nSend /login to get started.",
                None,
            )
            .await;
        return;
    };

    if !db::user_telegram_session::has_session(&state.pool_ro, mediafusion_user_id).await {
        let _ = api
            .send_message(
                chat_id,
                "🔐 *Session Required*\n\nConnect your Telegram scraping session on the web UI first.\n\nSend /session for instructions.",
                None,
            )
            .await;
        return;
    }

    let progress = api
        .send_message(
            chat_id,
            "🔍 Loading channels from your Telegram account...",
            None,
        )
        .await;

    let dialogs = match telegram_dialogs::list_scrapable_dialogs(
        &state.pool,
        &state.telegram_clients,
        mediafusion_user_id,
        40,
    )
    .await
    {
        Ok(list) => list,
        Err(e) => {
            if let Ok(mid) = progress {
                let _ = api
                    .edit_message_text(
                        chat_id,
                        mid,
                        &format!("❌ Failed to load dialogs: {e}"),
                        None,
                    )
                    .await;
            }
            return;
        }
    };

    let scrapable: Vec<_> = dialogs.into_iter().filter(|d| d.scrapable).collect();
    if scrapable.is_empty() {
        let msg = "ℹ️ *No Channels or Groups Found*\n\n\
             Your Telegram account has no channels or groups in recent dialogs.\n\n\
             Join a channel or group in Telegram first, then try again.";
        if let Ok(mid) = progress {
            let _ = api.edit_message_text(chat_id, mid, msg, None).await;
        }
        return;
    }

    state_store::save_dialog_pick(state, telegram_user_id, &scrapable).await;
    let (text, keyboard) = build_picker_view(state, telegram_user_id, &scrapable, 0).await;
    if let Ok(mid) = progress {
        let _ = api
            .edit_message_text(chat_id, mid, &text, Some(keyboard))
            .await;
    }
}

pub async fn handle_dialog_pick(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
    message_id: i64,
    index: usize,
) {
    let Some((mediafusion_user_id, _)) =
        db::telegram::get_user_by_telegram_id(&state.pool_ro, telegram_user_id).await
    else {
        return;
    };

    let Some(dialogs) = state_store::get_dialog_pick(state, telegram_user_id).await else {
        let _ = api
            .edit_message_text(
                chat_id,
                message_id,
                "⏳ Session expired. Send /browsechannels again.",
                None,
            )
            .await;
        return;
    };

    let Some(dialog) = dialogs.get(index) else {
        return;
    };

    match telegram_channels::add_user_channel(
        &state.pool,
        mediafusion_user_id,
        &dialog.id,
        Some(&dialog.name),
    )
    .await
    {
        Ok(ch) => {
            state_store::clear_dialog_pick(state, telegram_user_id).await;
            let _ = api
                .edit_message_text(
                    chat_id,
                    message_id,
                    &format!(
                        "✅ *Channel Added*\n\n\
                         {name} ({id}) is saved for scraping.\n\n\
                         Run /scrape to scrape all channels or /scrape {id} for just this one.",
                        name = ch.name,
                        id = ch.id
                    ),
                    None,
                )
                .await;
        }
        Err(crate::db::telegram_channels::ChannelMutationError::Duplicate) => {
            let _ = api
                .edit_message_text(
                    chat_id,
                    message_id,
                    &format!(
                        "ℹ️ {name} is already in your scraping list.",
                        name = dialog.name
                    ),
                    None,
                )
                .await;
        }
        Err(e) => {
            tracing::error!("dialog pick add channel: {e:?}");
            let _ = api
                .edit_message_text(chat_id, message_id, "❌ Failed to add channel.", None)
                .await;
        }
    }
}

pub async fn handle_dialog_page(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
    message_id: i64,
    page: usize,
) {
    let Some(dialogs) = state_store::get_dialog_pick(state, telegram_user_id).await else {
        let _ = api
            .edit_message_text(
                chat_id,
                message_id,
                "⏳ Session expired. Send /browsechannels again.",
                None,
            )
            .await;
        return;
    };

    let (text, keyboard) = build_picker_view(state, telegram_user_id, &dialogs, page).await;
    let _ = api
        .edit_message_text(chat_id, message_id, &text, Some(keyboard))
        .await;
}

async fn build_picker_view(
    state: &AppState,
    telegram_user_id: i64,
    dialogs: &[telegram_dialogs::ScrapableDialog],
    page: usize,
) -> (String, serde_json::Value) {
    let total_pages = ((dialogs.len() + PAGE_SIZE - 1) / PAGE_SIZE).max(1);
    let page = page.min(total_pages - 1);
    let start = page * PAGE_SIZE;
    let end = (start + PAGE_SIZE).min(dialogs.len());

    let mut rows = vec![];
    for (offset, dialog) in dialogs[start..end].iter().enumerate() {
        let index = start + offset;
        let visibility = if dialog.is_public {
            dialog.id.clone()
        } else {
            "private".to_string()
        };
        let label = truncate_label(&format!(
            "{} ({}, {})",
            dialog.name, dialog.kind, visibility
        ));
        rows.push(json!([{
            "text": label,
            "callback_data": CallbackAction::DialogPick {
                user_id: telegram_user_id,
                index,
            }.encode(state).await,
        }]));
    }

    let mut nav = vec![];
    if page > 0 {
        nav.push(json!({
            "text": "◀ Prev",
            "callback_data": CallbackAction::DialogPage {
                user_id: telegram_user_id,
                page: page - 1,
            }.encode(state).await,
        }));
    }
    if page + 1 < total_pages {
        nav.push(json!({
            "text": "Next ▶",
            "callback_data": CallbackAction::DialogPage {
                user_id: telegram_user_id,
                page: page + 1,
            }.encode(state).await,
        }));
    }
    if !nav.is_empty() {
        rows.push(json!(nav));
    }

    rows.push(json!([{
        "text": "❌ Cancel",
        "callback_data": CallbackAction::DialogCancel { user_id: telegram_user_id }.encode(state).await,
    }]));

    (
        format!(
            "📂 *Pick a Channel or Group*\n\n\
             Tap one to add it to your scraping list (page {}/{}).\n\
             Public entries show @username; private ones are scraped via your connected session.",
            page + 1,
            total_pages
        ),
        json!({ "inline_keyboard": rows }),
    )
}

fn truncate_label(label: &str) -> String {
    if label.chars().count() <= 40 {
        label.to_string()
    } else {
        format!("{}…", label.chars().take(37).collect::<String>())
    }
}
