//! Contribution wizard flow.

use serde_json::{Value, json};

use crate::state::AppState;

use super::{
    analyze,
    api::BotApi,
    batch,
    callback::CallbackAction,
    content_exists, detect, import, matches,
    metadata::{field_options, is_valid_poster_url},
    model::{ContentType, ConversationState, ConversationStep},
    state_store, text,
};

pub async fn start_wizard(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    original_message_id: i64,
    content_type: ContentType,
    raw_input: Value,
    batch_item_id: Option<String>,
) {
    if let Some(existing) = state_store::get_conversation(state, user_id).await {
        if !existing.is_expired(30) {
            let _ = api
                .send_message(
                    chat_id,
                    "ℹ️ You already have an active import. Send `/cancel` first.",
                    None,
                )
                .await;
            return;
        }
        state_store::clear_conversation(state, user_id).await;
    }

    let preview = detect::content_preview(content_type, &raw_input);
    let (msg, keyboard) =
        matches::show_media_type_picker(state, user_id, content_type, &preview).await;

    let message_id = match api.send_message(chat_id, &msg, Some(keyboard)).await {
        Ok(id) => id,
        Err(e) => {
            tracing::warn!("wizard start message: {e}");
            return;
        }
    };

    let mut conv = ConversationState::new(user_id, chat_id);
    conv.step = ConversationStep::AwaitingMediaType;
    conv.content_type = Some(content_type);
    conv.raw_input = raw_input;
    conv.message_id = Some(message_id);
    conv.original_message_id = Some(original_message_id);
    conv.batch_item_id = batch_item_id;
    state_store::save_conversation(state, &conv).await;
}

pub async fn handle_media_type_selection(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
    media_type: &str,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };

    conv.media_type = Some(media_type.to_string());
    conv.step = ConversationStep::Analyzing;
    conv.touch();
    state_store::save_conversation(state, &conv).await;

    let _ = api
        .edit_message_text(chat_id, message_id, "⏳ *Analyzing content...*", None)
        .await;

    let content_type = conv.content_type.unwrap_or(ContentType::Magnet);
    let analysis =
        analyze::run_analysis(state, api, content_type, &conv.raw_input, media_type).await;

    if analysis.get("success").and_then(|v| v.as_bool()) == Some(false) {
        let err = analysis
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Analysis failed");
        let _ = api
            .edit_message_text(
                chat_id,
                message_id,
                &format!("❌ *Analysis Error*\n\n{err}\n\n_Please try again._"),
                None,
            )
            .await;
        state_store::clear_conversation(state, user_id).await;
        return;
    }

    conv.analysis_result = Some(analysis.clone());
    conv.matches = analysis.get("matches").and_then(|v| v.as_array()).cloned();

    if let Some(reason) = content_exists::check_content_already_exists(&state.pool, &conv).await {
        let _ = api
            .edit_message_text(
                chat_id,
                message_id,
                &format!(
                    "ℹ️ *Already Available*\n\n{reason}\n\nThis content is already in MediaFusion, so import is skipped."
                ),
                None,
            )
            .await;
        state_store::clear_conversation(state, user_id).await;
        return;
    }

    if media_type == "sports" {
        conv.step = ConversationStep::AwaitingSportsCategory;
        conv.touch();
        state_store::save_conversation(state, &conv).await;
        let (msg, kb) = matches::show_sports_category_picker(state, user_id).await;
        let _ = api
            .edit_message_text(chat_id, message_id, &msg, Some(kb))
            .await;
        return;
    }

    conv.step = ConversationStep::AwaitingMatch;
    conv.touch();
    state_store::save_conversation(state, &conv).await;
    let (msg, kb) = matches::show_matches(state, &conv).await;
    let _ = api
        .edit_message_text(chat_id, message_id, &msg, Some(kb))
        .await;
}

pub async fn handle_sports_category(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
    category: &str,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    conv.sports_category = Some(category.to_string());
    conv.step = ConversationStep::AwaitingMetadataReview;
    let analysis = conv.analysis_result.clone().unwrap_or(json!({}));
    let title = analysis
        .get("parsed_title")
        .or_else(|| analysis.get("file_name"))
        .and_then(|v| v.as_str())
        .unwrap_or("Sports Event");
    conv.selected_match = Some(json!({
        "external_id": format!("sports_{category}"),
        "title": title,
        "type": "sports",
    }));
    conv.touch();
    state_store::save_conversation(state, &conv).await;
    let (msg, kb) = matches::show_metadata_review(state, &conv).await;
    let _ = api
        .edit_message_text(chat_id, message_id, &msg, Some(kb))
        .await;
}

pub async fn handle_match_selection(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
    external_id: &str,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    let media_type = conv.media_type.as_deref().unwrap_or("movie");
    let analysis = conv.analysis_result.clone().unwrap_or(json!({}));
    let fallback_title = analysis
        .get("parsed_title")
        .or_else(|| analysis.get("torrent_name"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let year = analysis
        .get("year")
        .and_then(|v| v.as_i64())
        .map(|y| y as i32);

    let selected =
        matches::resolve_external_id(state, external_id, media_type, fallback_title, year)
            .await
            .or_else(|| {
                conv.matches
                    .as_ref()?
                    .iter()
                    .find(|m| {
                        m.get("external_id")
                            .and_then(|v| v.as_str())
                            .map(|id| id == external_id)
                            .unwrap_or(false)
                    })
                    .cloned()
            });

    let Some(selected) = selected else {
        let _ = api
            .edit_message_text(chat_id, message_id, "❌ Match not found.", None)
            .await;
        return;
    };

    conv.selected_match = Some(selected);
    conv.step = ConversationStep::AwaitingMetadataReview;
    conv.touch();
    state_store::save_conversation(state, &conv).await;
    let (msg, kb) = matches::show_metadata_review(state, &conv).await;
    let _ = api
        .edit_message_text(chat_id, message_id, &msg, Some(kb))
        .await;
}

pub async fn handle_text_input(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    text_input: &str,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    let message_id = conv.message_id.unwrap_or(0);

    match conv.step {
        ConversationStep::AwaitingManualImdb | ConversationStep::AwaitingTitleSearch => {
            if conv.step == ConversationStep::AwaitingTitleSearch {
                let media_type = conv.media_type.as_deref().unwrap_or("movie");
                let results =
                    matches::search_by_title(state, text_input.trim(), None, media_type).await;
                conv.matches = Some(results);
                conv.step = ConversationStep::AwaitingMatch;
            } else if let Some(ext_id) = matches::parse_external_id_from_text(text_input) {
                conv.step = ConversationStep::AwaitingMatch;
                state_store::save_conversation(state, &conv).await;
                handle_match_selection(state, api, user_id, chat_id, message_id, &ext_id).await;
                return;
            } else {
                let _ = api
                    .send_message(chat_id, "❌ Invalid external ID format.", None)
                    .await;
                return;
            }
            conv.touch();
            state_store::save_conversation(state, &conv).await;
            let (msg, kb) = matches::show_matches(state, &conv).await;
            let _ = api
                .edit_message_text(chat_id, message_id, &msg, Some(kb))
                .await;
        }
        ConversationStep::AwaitingEpisodeInput => {
            let field = conv.editing_field.clone().unwrap_or_default();
            if field != "season_number" && field != "episode_number" {
                return;
            }
            let stripped = text_input.trim();
            if !stripped.chars().all(|c| c.is_ascii_digit()) {
                let label = if field == "season_number" {
                    "Season"
                } else {
                    "Episode"
                };
                let _ = api
                    .send_message(
                        chat_id,
                        &format!("Please enter a valid number for {label}."),
                        None,
                    )
                    .await;
                return;
            }
            if let Some(obj) = conv.metadata_overrides.as_object_mut() {
                obj.insert(
                    field.clone(),
                    json!(stripped.parse::<i64>().unwrap_or_default()),
                );
            }
            conv.editing_field = None;
            conv.step = ConversationStep::AwaitingMetadataReview;
            conv.touch();
            state_store::save_conversation(state, &conv).await;
            let (msg, kb) = matches::show_metadata_review(state, &conv).await;
            let _ = api
                .edit_message_text(chat_id, message_id, &msg, Some(kb))
                .await;
        }
        ConversationStep::AwaitingPosterInput => {
            handle_poster_input(state, api, user_id, chat_id, text_input.trim()).await;
        }
        ConversationStep::AwaitingAnonymousName => {
            if text_input.trim().to_lowercase() != "skip" {
                conv.anonymous_display_name = Some(text_input.trim().chars().take(50).collect());
            }
            conv.step = ConversationStep::Importing;
            conv.touch();
            state_store::save_conversation(state, &conv).await;
            run_import(state, api, user_id, chat_id, message_id, &conv).await;
        }
        _ => {}
    }
}

pub async fn handle_poster_photo(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    file_id: &str,
) {
    let Some(conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    if conv.step != ConversationStep::AwaitingPosterInput {
        return;
    }

    let file_info = match api.get_file(file_id).await {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("poster get_file: {e}");
            let _ = api
                .send_message(
                    chat_id,
                    "⚠️ Failed to process uploaded image. Please try sending an image URL instead.",
                    None,
                )
                .await;
            return;
        }
    };
    let Some(file_path) = file_info.get("file_path").and_then(|v| v.as_str()) else {
        let _ = api
            .send_message(
                chat_id,
                "⚠️ Failed to process uploaded image. Please try sending an image URL instead.",
                None,
            )
            .await;
        return;
    };
    let token = state.config.telegram_bot_token.as_deref().unwrap_or("");
    let poster_url = format!("https://api.telegram.org/file/bot{token}/{file_path}");
    handle_poster_input(state, api, user_id, chat_id, &poster_url).await;
}

async fn handle_poster_input(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    poster_url: &str,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    if !is_valid_poster_url(poster_url) {
        let _ = api
            .send_message(
                chat_id,
                "⚠️ *Invalid Image URL*\n\nPlease send a valid image URL (jpg, png, webp) or upload an image directly.",
                None,
            )
            .await;
        return;
    }

    conv.custom_poster_url = Some(poster_url.to_string());
    conv.step = ConversationStep::AwaitingMetadataReview;
    conv.touch();
    state_store::save_conversation(state, &conv).await;

    let _ = api
        .send_message(
            chat_id,
            "✅ *Poster Added*\n\nCustom poster has been set.",
            None,
        )
        .await;

    if let Some(message_id) = conv.message_id {
        let (msg, kb) = matches::show_metadata_review(state, &conv).await;
        let _ = api
            .edit_message_text(chat_id, message_id, &msg, Some(kb))
            .await;
    }
}

pub async fn handle_manual_imdb_prompt(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    conv.step = ConversationStep::AwaitingManualImdb;
    conv.touch();
    state_store::save_conversation(state, &conv).await;
    let kb = super::callback::cancel_keyboard(state, user_id).await;
    let _ = api
        .edit_message_text(
            chat_id,
            message_id,
            "✏️ *Manual Entry*\n\nPlease reply with an external ID.\n\n*Examples:* `tt1234567`, `tmdb:12345`",
            Some(kb),
        )
        .await;
}

pub async fn handle_title_search_prompt(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    conv.step = ConversationStep::AwaitingTitleSearch;
    conv.touch();
    state_store::save_conversation(state, &conv).await;
    let kb = super::callback::cancel_keyboard(state, user_id).await;
    let _ = api
        .edit_message_text(
            chat_id,
            message_id,
            "🔍 *Search by Title*\n\nType the title of the movie or series.",
            Some(kb),
        )
        .await;
}

pub async fn handle_confirm_import(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };

    if let Some(uid) =
        crate::db::telegram::resolve_mediafusion_user_id(&state.pool, &state.redis, conv.user_id)
            .await
        && let Some(user_info) = crate::routes::content::import_helpers::fetch_user_info(
            &state.pool,
            i64::from(i32::from(uid)),
        )
        .await
        && user_info.contribute_anonymously
        && conv.anonymous_display_name.is_none()
    {
        conv.step = ConversationStep::AwaitingAnonymousName;
        conv.touch();
        state_store::save_conversation(state, &conv).await;
        let keyboard = json!({
            "inline_keyboard": [
                [{"text": "⏭ Skip (use Anonymous)", "callback_data": CallbackAction::AnonSkip { user_id }.encode(state).await}],
                [{"text": "◀️ Back to Review", "callback_data": CallbackAction::BackReview { user_id }.encode(state).await}],
                [{"text": "❌ Cancel", "callback_data": CallbackAction::Cancel { user_id }.encode(state).await}],
            ]
        });
        let _ = api
            .edit_message_text(
                chat_id,
                message_id,
                "🕶️ *Anonymous Contribution*\n\nSend a custom display name for this contribution.\n\
                     • Max 50 chars\n\n_Send text now, or tap Skip to use Anonymous._",
                Some(keyboard),
            )
            .await;
        return;
    }

    let _ = api
        .edit_message_text(chat_id, message_id, "⏳ *Importing...*", None)
        .await;

    run_import(state, api, user_id, chat_id, message_id, &conv).await;
    state_store::clear_conversation(state, user_id).await;
}

async fn run_import(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
    conv: &ConversationState,
) {
    match import::execute_import(state, api, conv).await {
        Ok(msg) => {
            let _ = api.edit_message_text(chat_id, message_id, &msg, None).await;
            if let Some(item_id) = conv.batch_item_id.clone() {
                batch::finish_item_review(state, api, user_id, &item_id, true).await;
            }
        }
        Err(e) => {
            let _ = api
                .edit_message_text(
                    chat_id,
                    message_id,
                    &format!("❌ *Import Failed*\n\n{e}"),
                    None,
                )
                .await;
        }
    }
}

pub async fn handle_anon_skip(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    conv.anonymous_display_name = None;
    conv.step = ConversationStep::Importing;
    conv.touch();
    state_store::save_conversation(state, &conv).await;

    let _ = api
        .edit_message_text(chat_id, message_id, "⏳ *Importing...*", None)
        .await;
    run_import(state, api, user_id, chat_id, message_id, &conv).await;
    state_store::clear_conversation(state, user_id).await;
}

pub async fn handle_cancel(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let batch_item_id = state_store::get_conversation(state, user_id)
        .await
        .and_then(|c| c.batch_item_id.clone());
    state_store::clear_conversation(state, user_id).await;
    if let Some(item_id) = batch_item_id {
        batch::finish_item_review(state, api, user_id, &item_id, false).await;
        let _ = api
            .edit_message_text(chat_id, message_id, "↩️ *Returned to batch.*", None)
            .await;
    } else {
        let _ = api
            .edit_message_text(chat_id, message_id, &text::cancel_success(), None)
            .await;
    }
}

pub async fn handle_back(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    conv.step = ConversationStep::AwaitingMatch;
    conv.touch();
    state_store::save_conversation(state, &conv).await;
    let (msg, kb) = matches::show_matches(state, &conv).await;
    let _ = api
        .edit_message_text(chat_id, message_id, &msg, Some(kb))
        .await;
}

pub async fn handle_back_to_review(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    conv.step = ConversationStep::AwaitingMetadataReview;
    conv.editing_field = None;
    conv.touch();
    state_store::save_conversation(state, &conv).await;
    let (msg, kb) = matches::show_metadata_review(state, &conv).await;
    let _ = api
        .edit_message_text(chat_id, message_id, &msg, Some(kb))
        .await;
}

pub async fn handle_add_poster_prompt(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    conv.step = ConversationStep::AwaitingPosterInput;
    conv.touch();
    state_store::save_conversation(state, &conv).await;

    let mut rows = vec![];
    if conv.custom_poster_url.is_some() {
        rows.push(json!([{
            "text": "🗑️ Clear Custom Poster",
            "callback_data": CallbackAction::ClearPoster { user_id }.encode(state).await,
        }]));
    }
    rows.push(json!([{
        "text": "◀️ Back to Review",
        "callback_data": CallbackAction::BackReview { user_id }.encode(state).await,
    }]));

    let _ = api
        .edit_message_text(
            chat_id,
            message_id,
            "🖼️ *Add Custom Poster*\n\n\
         You can provide a poster image in one of the following ways:\n\n\
         • *Send an image URL* - Paste a direct link to an image (jpg, png, webp)\n\
         • *Upload an image* - Send an image file directly\n\n\
         _The image will be used as the poster for this content._",
            Some(json!({ "inline_keyboard": rows })),
        )
        .await;
}

pub async fn handle_clear_poster(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    conv.custom_poster_url = None;
    conv.step = ConversationStep::AwaitingMetadataReview;
    conv.touch();
    state_store::save_conversation(state, &conv).await;
    let (msg, kb) = matches::show_metadata_review(state, &conv).await;
    let _ = api
        .edit_message_text(chat_id, message_id, &msg, Some(kb))
        .await;
}

pub async fn handle_meta_edit(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
    field: &str,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };

    if field == "season_number" || field == "episode_number" {
        conv.step = ConversationStep::AwaitingEpisodeInput;
        conv.editing_field = Some(field.to_string());
        conv.touch();
        state_store::save_conversation(state, &conv).await;
        let label = if field == "season_number" {
            "Season Number"
        } else {
            "Episode Number"
        };
        let keyboard = json!({
            "inline_keyboard": [[{
                "text": "◀️ Back to Review",
                "callback_data": CallbackAction::BackReview { user_id }.encode(state).await,
            }]]
        });
        let _ = api
            .edit_message_text(
                chat_id,
                message_id,
                &format!(
                    "📺 *Enter {label}*\n\nReply with the {} (a number).\n\n*Example:* `3`",
                    label.to_lowercase()
                ),
                Some(keyboard),
            )
            .await;
        return;
    }

    conv.step = ConversationStep::AwaitingFieldEdit;
    conv.editing_field = Some(field.to_string());
    conv.touch();
    state_store::save_conversation(state, &conv).await;

    let options = field_options(field);
    let mut rows: Vec<Value> = vec![];
    for chunk in options.chunks(2) {
        let mut row = vec![];
        for v in chunk {
            row.push(json!({
                "text": v,
                "callback_data": CallbackAction::MetaVal {
                    user_id,
                    field: field.to_string(),
                    value: v.to_string(),
                }.encode(state).await,
            }));
        }
        rows.push(json!(row));
    }
    rows.push(json!([{
        "text": "◀️ Back to Review",
        "callback_data": CallbackAction::BackReview { user_id }.encode(state).await,
    }]));

    let _ = api
        .edit_message_text(
            chat_id,
            message_id,
            &format!("✏️ *Select {field}:*"),
            Some(json!({ "inline_keyboard": rows })),
        )
        .await;
}

pub async fn handle_meta_val(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
    field: &str,
    value: &str,
) {
    let Some(mut conv) = state_store::get_conversation(state, user_id).await else {
        return;
    };
    if let Some(obj) = conv.metadata_overrides.as_object_mut() {
        obj.insert(field.to_string(), json!(value));
    }
    conv.step = ConversationStep::AwaitingMetadataReview;
    conv.editing_field = None;
    conv.touch();
    state_store::save_conversation(state, &conv).await;
    let (msg, kb) = matches::show_metadata_review(state, &conv).await;
    let _ = api
        .edit_message_text(chat_id, message_id, &msg, Some(kb))
        .await;
}

pub async fn handle_batch_summary(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let batch_item_id = state_store::get_conversation(state, user_id)
        .await
        .and_then(|c| c.batch_item_id.clone());
    state_store::clear_conversation(state, user_id).await;
    if let Some(item_id) = batch_item_id {
        batch::finish_item_review(state, api, user_id, &item_id, false).await;
    }
    let _ = api
        .edit_message_text(chat_id, message_id, "↩️ *Returned to batch.*", None)
        .await;
    if let Some(batch) = state_store::get_batch(state, user_id).await {
        batch::render_batch_summary(state, api, &batch).await;
    }
}
