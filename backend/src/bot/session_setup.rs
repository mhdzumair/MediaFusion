//! Bot /session — directs users to the web UI for Telegram session login.

use crate::{
    db::{self, types::UserId},
    state::AppState,
};

use super::{api::BotApi, text};

pub async fn handle_session_command(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
) {
    if chat_id != telegram_user_id {
        let _ = api
            .send_message(chat_id, &text::login_private_chat_required(), None)
            .await;
        return;
    }

    if db::telegram::get_user_by_telegram_id(&state.pool_ro, telegram_user_id)
        .await
        .is_none()
    {
        let _ = api
            .send_message(
                chat_id,
                "🔐 *Account Required*\n\nLink your MediaFusion account first.\n\nSend /login to get started.",
                None,
            )
            .await;
        return;
    }

    let _ = api
        .send_message(
            chat_id,
            &text::session_web_instructions(&state.config.host_url),
            None,
        )
        .await;
}

pub async fn handle_drop_session_command(
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

    match crate::services::telegram_login::delete_user_session(
        &state.pool,
        &state.telegram_pending_logins,
        &state.telegram_clients,
        mediafusion_user_id,
    )
    .await
    {
        Ok(true) => {
            let _ = api
                .send_message(
                    chat_id,
                    "✅ *Session Disconnected*\n\nYour Telegram scraping session was removed.",
                    None,
                )
                .await;
        }
        Ok(false) => {
            let _ = api
                .send_message(
                    chat_id,
                    "ℹ️ *No Session Connected*\n\nYou do not have an active Telegram scraping session.",
                    None,
                )
                .await;
        }
        Err(e) => {
            tracing::error!("drop session: {e}");
            let _ = api
                .send_message(chat_id, "❌ Failed to disconnect session.", None)
                .await;
        }
    }
}

pub async fn clear_if_active(_state: &AppState, _telegram_user_id: i64) -> bool {
    false
}

pub async fn notify_session_connected(
    state: &AppState,
    mediafusion_user_id: UserId,
    telegram_account_id: i64,
) {
    let row: Option<(Option<i64>,)> =
        sqlx::query_as("SELECT telegram_user_id FROM users WHERE id = $1")
            .bind(mediafusion_user_id.0)
            .fetch_optional(&state.pool_ro)
            .await
            .ok()
            .flatten();

    let Some(telegram_user_id) = row.and_then(|(id,)| id) else {
        return;
    };

    let Ok(api) = BotApi::from_state(state) else {
        return;
    };

    let _ = api
        .send_message(
            telegram_user_id,
            &text::session_connected_notification(telegram_account_id),
            None,
        )
        .await;
}
