//! Webhook update dispatcher.

use std::sync::Arc;

use tracing::warn;

use crate::state::AppState;

use super::{
    api::BotApi, callback::CallbackAction, commands, detect, model::Message, state_store, wizard,
};

pub async fn dispatch_update(state: Arc<AppState>, update: super::model::Update) {
    let api = match BotApi::from_state(&state) {
        Ok(a) => a,
        Err(e) => {
            warn!("bot dispatch: {e}");
            return;
        }
    };

    if let Some(cb) = update.callback_query {
        handle_callback(&state, &api, cb).await;
        return;
    }

    if let Some(msg) = update.message.or(update.edited_message) {
        handle_message(&state, &api, msg).await;
    }
}

async fn handle_message(state: &AppState, api: &BotApi, message: Message) {
    let from = match &message.from {
        Some(u) if !u.is_bot => u.id,
        _ => return,
    };
    let user_id = from;
    let chat_id = message.chat.id;

    let text_or_caption = message.text.as_deref().or(message.caption.as_deref());

    if let Some(conv) = state_store::get_conversation(state, user_id).await {
        if conv.step == super::model::ConversationStep::AwaitingPosterInput {
            if let Some(photos) = &message.photo {
                if let Some(largest) = photos.last() {
                    wizard::handle_poster_photo(state, api, user_id, chat_id, &largest.file_id)
                        .await;
                    return;
                }
            }
        }
    }

    if let Some(text) = text_or_caption {
        if text.starts_with('/') {
            commands::handle_command(state, api, user_id, chat_id, text).await;
            return;
        }

        if state_store::get_conversation(state, user_id)
            .await
            .is_some()
        {
            wizard::handle_text_input(state, api, user_id, chat_id, text).await;
            return;
        }

        // Check if user is in batch series/season input mode
        if let Some(batch) = state_store::get_batch(state, user_id).await {
            if batch.awaiting_season_input {
                super::batch::handle_season_input(state, api, user_id, chat_id, text).await;
                return;
            }
            if batch.awaiting_series_input {
                super::batch::handle_series_input(state, api, user_id, chat_id, text).await;
                return;
            }
        }
    }

    if let Some((content_type, raw)) = detect::detect_content_type(&message) {
        if super::disabled_content::is_content_type_disabled(
            content_type,
            &state.config.disabled_content_types,
        ) {
            let label = super::disabled_content::content_type_label(content_type);
            let _ = api
                .send_message(
                    chat_id,
                    &format!(
                        "🚫 *{label}* imports are currently disabled on this instance.\n\nType `/help` to see which content types are available."
                    ),
                    None,
                )
                .await;
            return;
        }

        let is_forwarded =
            message.forward_from_chat.is_some() || message.forward_from_message_id.is_some();
        if is_forwarded
            && state_store::get_conversation(state, user_id)
                .await
                .is_none()
        {
            super::batch::append_forwarded_video(state, api, user_id, chat_id, raw).await;
            return;
        }
        commands::handle_content_message(
            state,
            api,
            user_id,
            chat_id,
            message.message_id,
            content_type,
            raw,
        )
        .await;
    }
}

async fn handle_callback(state: &AppState, api: &BotApi, cb: super::model::CallbackQuery) {
    let user_id = cb.from.id;
    let data = match cb.data.as_deref() {
        Some(d) => d,
        None => return,
    };

    let action = match CallbackAction::decode(data, state).await {
        Some(a) => a,
        None => {
            let _ = api
                .answer_callback_query(&cb.id, Some("Unknown action"), true)
                .await;
            return;
        }
    };

    if action.target_user_id() != user_id {
        let _ = api
            .answer_callback_query(&cb.id, Some(&super::text::unauthorized()), true)
            .await;
        return;
    }

    let message = match cb.message {
        Some(m) => m,
        None => return,
    };
    let chat_id = message.chat.id;
    let message_id = message.message_id;

    let _ = api.answer_callback_query(&cb.id, None, false).await;

    match action {
        CallbackAction::MediaType { media_type, .. } => {
            wizard::handle_media_type_selection(
                state,
                api,
                user_id,
                chat_id,
                message_id,
                &media_type,
            )
            .await;
        }
        CallbackAction::Match { external_id, .. } => {
            wizard::handle_match_selection(state, api, user_id, chat_id, message_id, &external_id)
                .await;
        }
        CallbackAction::Sport { category, .. } => {
            wizard::handle_sports_category(state, api, user_id, chat_id, message_id, &category)
                .await;
        }
        CallbackAction::SearchTitle { .. } => {
            wizard::handle_title_search_prompt(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::Manual { .. } => {
            wizard::handle_manual_imdb_prompt(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::Confirm { .. } => {
            wizard::handle_confirm_import(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::Cancel { .. } => {
            wizard::handle_cancel(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::Back { .. } => {
            wizard::handle_back(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::BackReview { .. } => {
            wizard::handle_back_to_review(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::AnonSkip { .. } => {
            wizard::handle_anon_skip(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::AddPoster { .. } => {
            wizard::handle_add_poster_prompt(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::ClearPoster { .. } => {
            wizard::handle_clear_poster(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::BatchSummary { .. } => {
            wizard::handle_batch_summary(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::BatchReview { item_id, .. } => {
            super::batch::start_batch_item_review(state, api, user_id, chat_id, &item_id).await;
        }
        CallbackAction::MetaEdit { field, .. } => {
            wizard::handle_meta_edit(state, api, user_id, chat_id, message_id, &field).await;
        }
        CallbackAction::MetaVal { field, value, .. } => {
            wizard::handle_meta_val(state, api, user_id, chat_id, message_id, &field, &value).await;
        }
        CallbackAction::BatchImport { .. } => {
            super::batch::handle_batch_import(state, api, user_id, chat_id).await;
        }
        CallbackAction::BatchSkip { item_id, .. } => {
            super::batch::skip_item(state, api, user_id, &item_id).await;
        }
        CallbackAction::BatchSetSeries { .. } => {
            super::batch::handle_set_series_prompt(state, api, user_id, chat_id, message_id).await;
        }
        CallbackAction::LegacySelect { .. } => {}
    }
}

pub async fn register_commands(state: Arc<AppState>) {
    let api = match BotApi::from_state(&state) {
        Ok(a) => a,
        Err(_) => return,
    };
    let commands = [
        ("start", "Welcome message and quick start guide"),
        ("help", "Show available commands and usage"),
        ("login", "Link your Telegram account to MediaFusion"),
        ("status", "Check account link status"),
        ("cancel", "Cancel current operation"),
        (
            "scrape",
            "Scrape a public Telegram channel (@channel or t.me link)",
        ),
    ];
    match api.set_my_commands(&commands).await {
        Ok(()) => tracing::info!("telegram bot commands registered"),
        Err(e) => tracing::warn!("telegram bot register commands: {e}"),
    }
}
