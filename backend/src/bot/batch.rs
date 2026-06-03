//! Batch forwarded-video contributions.

use serde_json::json;
use uuid::Uuid;

use crate::state::AppState;

use super::{
    api::BotApi,
    callback::CallbackAction,
    import,
    model::{
        BatchItem, BatchItemStatus, BatchSeriesContext, BatchState, ContentType, ConversationState,
        ConversationStep,
    },
    state_store, wizard,
};

pub async fn append_forwarded_video(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    raw_input: serde_json::Value,
) {
    let mut batch = state_store::get_batch(state, user_id)
        .await
        .unwrap_or_else(|| BatchState {
            batch_id: Uuid::new_v4().to_string(),
            user_id,
            chat_id,
            items: vec![],
            summary_message_id: None,
            editing_item_id: None,
            series_context: None,
            awaiting_series_input: false,
            awaiting_season_input: false,
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
        });

    let file_id = raw_input
        .get("file_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    batch.items.push(BatchItem {
        item_id: Uuid::new_v4().to_string(),
        file_id,
        file_unique_id: raw_input
            .get("file_unique_id")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        file_name: raw_input
            .get("file_name")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        mime_type: raw_input
            .get("mime_type")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        file_size: raw_input.get("file_size").and_then(|v| v.as_i64()),
        chat_id,
        original_message_id: None,
        status: BatchItemStatus::PendingAnalysis,
        inferred_media_type: None,
        analysis_result: None,
        imdb_candidates: None,
        selected_match: None,
        metadata_overrides: serde_json::json!({}),
        error: None,
        created_at: chrono::Utc::now(),
    });
    batch.touch();
    state_store::save_batch(state, &batch).await;
    render_batch_summary(state, api, &batch).await;
}

pub async fn finish_item_review(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    item_id: &str,
    success: bool,
) {
    let Some(mut batch) = state_store::get_batch(state, user_id).await else {
        return;
    };
    if let Some(item) = batch.get_item_mut(item_id) {
        item.status = if success {
            BatchItemStatus::Imported
        } else {
            BatchItemStatus::Skipped
        };
    }
    batch.editing_item_id = None;
    batch.touch();
    state_store::save_batch(state, &batch).await;
    render_batch_summary(state, api, &batch).await;
}

pub async fn start_batch_item_review(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    item_id: &str,
) {
    let Some(batch) = state_store::get_batch(state, user_id).await else {
        return;
    };
    let Some(item) = batch.get_item(item_id) else {
        return;
    };

    wizard::start_wizard(
        state,
        api,
        user_id,
        chat_id,
        item.original_message_id.unwrap_or(0),
        ContentType::Video,
        serde_json::json!({
            "file_id": item.file_id,
            "file_unique_id": item.file_unique_id,
            "file_name": item.file_name,
            "mime_type": item.mime_type,
            "file_size": item.file_size,
        }),
        Some(item_id.to_string()),
    )
    .await;
}

pub async fn skip_item(state: &AppState, api: &BotApi, user_id: i64, item_id: &str) {
    let Some(mut batch) = state_store::get_batch(state, user_id).await else {
        return;
    };
    if let Some(item) = batch.get_item_mut(item_id) {
        item.status = BatchItemStatus::Skipped;
    }
    batch.touch();
    state_store::save_batch(state, &batch).await;
    render_batch_summary(state, api, &batch).await;
}

pub async fn handle_batch_import(state: &AppState, api: &BotApi, user_id: i64, chat_id: i64) {
    let Some(batch) = state_store::get_batch(state, user_id).await else {
        return;
    };

    let pending_items: Vec<_> = batch
        .items
        .iter()
        .filter(|i| {
            matches!(
                i.status,
                BatchItemStatus::PendingAnalysis | BatchItemStatus::AutoMatched
            ) && i.selected_match.is_some()
        })
        .cloned()
        .collect();

    if pending_items.is_empty() {
        let _ = api
            .send_message(
                chat_id,
                "ℹ️ No items ready for import. Review items first.",
                None,
            )
            .await;
        return;
    }

    let mut imported = 0usize;
    let mut failed = 0usize;

    for item in &pending_items {
        let mut conv = ConversationState::new(user_id, chat_id);
        conv.step = ConversationStep::AwaitingConfirm;
        conv.content_type = Some(ContentType::Video);
        conv.raw_input = json!({
            "file_id": item.file_id,
            "file_unique_id": item.file_unique_id,
            "file_name": item.file_name,
            "mime_type": item.mime_type,
            "file_size": item.file_size,
        });
        conv.selected_match = item.selected_match.clone();
        conv.metadata_overrides = item.metadata_overrides.clone();
        conv.media_type = item.inferred_media_type.clone();
        conv.batch_item_id = Some(item.item_id.clone());

        let api_ref = match super::api::BotApi::from_state(state) {
            Ok(a) => a,
            Err(_) => {
                failed += 1;
                continue;
            }
        };

        match import::execute_import(state, &api_ref, &conv).await {
            Ok(_) => {
                imported += 1;
                // Update item status in batch
                if let Some(mut batch2) = state_store::get_batch(state, user_id).await {
                    if let Some(it) = batch2.get_item_mut(&item.item_id) {
                        it.status = BatchItemStatus::Imported;
                    }
                    batch2.touch();
                    state_store::save_batch(state, &batch2).await;
                }
            }
            Err(_) => {
                failed += 1;
                if let Some(mut batch2) = state_store::get_batch(state, user_id).await {
                    if let Some(it) = batch2.get_item_mut(&item.item_id) {
                        it.status = BatchItemStatus::Failed;
                    }
                    batch2.touch();
                    state_store::save_batch(state, &batch2).await;
                }
            }
        }
    }

    let _ = api
        .send_message(
            chat_id,
            &format!("✅ *Batch Import Complete*\n\nImported: {imported}\nFailed: {failed}"),
            None,
        )
        .await;

    if let Some(batch) = state_store::get_batch(state, user_id).await {
        render_batch_summary(state, api, &batch).await;
    }
}

pub async fn handle_set_series_prompt(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    message_id: i64,
) {
    let Some(mut batch) = state_store::get_batch(state, user_id).await else {
        return;
    };
    batch.awaiting_series_input = true;
    batch.touch();
    state_store::save_batch(state, &batch).await;
    let kb = serde_json::json!({"inline_keyboard": [[{
        "text": "❌ Cancel",
        "callback_data": super::callback::CallbackAction::Cancel { user_id }.encode(state).await
    }]]});
    let _ = api.edit_message_text(
        chat_id,
        message_id,
        "📺 *Set TV Series for Batch*\n\nReply with the series title or IMDb/external ID.\n\n\
         *Examples:*\n\
         `Breaking Bad`\n\
         `tt0903747`\n\
         `tmdb:1396`\n\n\
         All batch files will be linked to this series and episodes will be inferred from filenames.",
        Some(kb),
    ).await;
}

pub async fn handle_series_input(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    input: &str,
) {
    let Some(mut batch) = state_store::get_batch(state, user_id).await else {
        return;
    };
    batch.awaiting_series_input = false;

    let input = input.trim();
    let matches = crate::routes::content::import_helpers::search_analyze_matches(
        state, None, input, None, "series",
    )
    .await;

    let selected = if matches.is_empty() {
        crate::routes::content::import_helpers::lookup_import_media_id_with_fallback(
            &state.pool,
            input,
            "series",
            input,
            None,
        )
        .await
        .map(|_media_id| {
            serde_json::json!({
                "external_id": input,
                "title": input,
                "type": "series",
            })
        })
    } else {
        matches.into_iter().next()
    };

    let Some(series_match) = selected else {
        let _ = api
            .send_message(
                chat_id,
                &format!(
                    "❌ *Series Not Found*\n\nCould not find `{input}`.\n\nTry again with a different title or IMDb ID."
                ),
                None,
            )
            .await;
        batch.awaiting_series_input = true;
        batch.touch();
        state_store::save_batch(state, &batch).await;
        return;
    };

    let title = series_match
        .get("title")
        .and_then(|v| v.as_str())
        .unwrap_or(input)
        .to_string();
    let external_id = series_match
        .get("external_id")
        .and_then(|v| v.as_str())
        .unwrap_or(input)
        .to_string();

    batch.series_context = Some(BatchSeriesContext {
        external_id,
        title: title.clone(),
        season: None,
    });
    batch.awaiting_season_input = true;
    batch.touch();
    state_store::save_batch(state, &batch).await;

    let _ = api
        .send_message(
            chat_id,
            &format!(
                "✅ *Series Found:* {title}\n\nNow reply with the *season number* (e.g. `1`, `2`):"
            ),
            None,
        )
        .await;
}

pub async fn handle_season_input(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    input: &str,
) {
    let Some(mut batch) = state_store::get_batch(state, user_id).await else {
        return;
    };

    let season: i32 = match input.trim().parse() {
        Ok(n) if n > 0 => n,
        _ => {
            let _ = api
                .send_message(
                    chat_id,
                    "❌ Invalid season number. Reply with a number like `1`.",
                    None,
                )
                .await;
            return;
        }
    };

    batch.awaiting_season_input = false;
    if let Some(ref mut ctx) = batch.series_context {
        ctx.season = Some(season);
    }

    let series_ctx = match &batch.series_context {
        Some(c) => c.clone(),
        None => return,
    };

    for item in batch.items.iter_mut() {
        if matches!(
            item.status,
            BatchItemStatus::PendingAnalysis | BatchItemStatus::NoMatch
        ) {
            let file_name = item.file_name.as_deref().unwrap_or("");
            let parsed = crate::parser::parse_title(file_name);
            let episode = parsed.episodes.first().copied();

            item.selected_match = Some(serde_json::json!({
                "external_id": series_ctx.external_id,
                "title": series_ctx.title,
                "type": "series",
            }));
            item.inferred_media_type = Some("series".to_string());
            item.status = BatchItemStatus::AutoMatched;
            item.metadata_overrides = serde_json::json!({
                "season": series_ctx.season,
                "episode": episode,
            });
        }
    }
    batch.touch();
    state_store::save_batch(state, &batch).await;

    let ready = batch
        .items
        .iter()
        .filter(|i| matches!(i.status, BatchItemStatus::AutoMatched))
        .count();
    let _ = api
        .send_message(
            chat_id,
            &format!(
                "✅ *Series Set*\n\n*{title}* Season {season}\n\n{ready} file(s) matched and ready to import.\n\nTap *Import All* to proceed.",
                title = series_ctx.title,
            ),
            None,
        )
        .await;

    render_batch_summary(state, api, &batch).await;
}

pub async fn render_batch_summary(state: &AppState, api: &BotApi, batch: &BatchState) {
    let pending = batch
        .items
        .iter()
        .filter(|i| {
            matches!(
                i.status,
                BatchItemStatus::PendingAnalysis | BatchItemStatus::NeedsReview
            )
        })
        .count();
    let imported = batch
        .items
        .iter()
        .filter(|i| matches!(i.status, BatchItemStatus::Imported))
        .count();
    let msg = format!(
        "📦 *Batch Summary*\n\nTotal: {}\nImported: {imported}\nPending: {pending}",
        batch.items.len()
    );

    // Build per-item buttons
    let mut rows: Vec<serde_json::Value> = vec![];
    for item in batch.items.iter().filter(|i| {
        matches!(
            i.status,
            BatchItemStatus::PendingAnalysis | BatchItemStatus::NeedsReview
        )
    }) {
        let label: String = item
            .file_name
            .as_deref()
            .unwrap_or("Video")
            .chars()
            .take(40)
            .collect();
        rows.push(json!([{"text": format!("📹 {label}"), "callback_data": CallbackAction::BatchReview { user_id: batch.user_id, item_id: item.item_id.clone() }.encode(state).await}]));
    }
    if !rows.is_empty() {
        rows.push(json!([{"text": "✅ Import All", "callback_data": CallbackAction::BatchImport { user_id: batch.user_id }.encode(state).await}]));
    }
    if !batch.items.is_empty() {
        rows.push(json!([{
            "text": "📺 Set as TV Series",
            "callback_data": CallbackAction::BatchSetSeries { user_id: batch.user_id }.encode(state).await
        }]));
    }
    rows.push(json!([{"text": "❌ Cancel Batch", "callback_data": CallbackAction::Cancel { user_id: batch.user_id }.encode(state).await}]));

    let keyboard = Some(json!({ "inline_keyboard": rows }));

    if let Some(mid) = batch.summary_message_id {
        let _ = api
            .edit_message_text(batch.chat_id, mid, &msg, keyboard)
            .await;
    } else {
        if let Ok(new_mid) = api.send_message(batch.chat_id, &msg, keyboard).await {
            if let Some(mut stored) = state_store::get_batch(state, batch.user_id).await {
                stored.summary_message_id = Some(new_mid);
                stored.touch();
                state_store::save_batch(state, &stored).await;
            }
        }
    }
}
