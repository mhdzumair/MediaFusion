//! /login command — generate account-linking tokens.

use crate::state::AppState;

use super::{api::BotApi, state_store, text};

pub struct LoginResult {
    pub success: bool,
    pub message: String,
}

pub async fn handle_login_command(
    state: &AppState,
    api: &BotApi,
    telegram_user_id: i64,
    chat_id: i64,
) -> LoginResult {
    if chat_id != telegram_user_id {
        return LoginResult {
            success: false,
            message: text::login_private_chat_required(),
        };
    }

    let login_token = uuid::Uuid::new_v4().simple().to_string();

    if let Err(e) = state_store::store_login_token(state, &login_token, telegram_user_id).await {
        tracing::error!("login token store: {e}");
        return LoginResult {
            success: false,
            message: "❌ Failed to generate login token. Please try again.".to_string(),
        };
    }

    let login_url = format!(
        "{}/app/telegram/login?token={login_token}",
        state.config.host_url.trim_end_matches('/')
    );

    let _ = api.get_me().await;

    LoginResult {
        success: true,
        message: text::login_success(&login_url, &login_token),
    }
}
