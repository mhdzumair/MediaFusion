//! Forwarded video storage and backup-channel copy.

use serde_json::Value;

use crate::{
    parser,
    routes::content::import_helpers::{self, UserInfo},
    state::AppState,
};

use super::{api::BotApi, model::ConversationState};

fn override_str<'a>(
    overrides: &'a Value,
    field: &str,
    fallback: Option<&'a str>,
) -> Option<&'a str> {
    overrides.get(field).and_then(|v| v.as_str()).or(fallback)
}

pub async fn store_forwarded_video(
    state: &AppState,
    api: &BotApi,
    conv: &ConversationState,
    mf_user_id: i64,
    user_info: &UserInfo,
    data: &Value,
) -> Result<(), String> {
    let overrides = &conv.metadata_overrides;
    let meta_id = data
        .get("meta_id")
        .and_then(|v| v.as_str())
        .ok_or("Missing meta_id")?;
    let file_id = data
        .get("file_id")
        .and_then(|v| v.as_str())
        .ok_or("Missing file_id")?;
    let file_name = data
        .get("file_name")
        .and_then(|v| v.as_str())
        .unwrap_or("video.mkv");
    let file_unique_id = data.get("file_unique_id").and_then(|v| v.as_str());

    let media_id: Option<i32> = import_helpers::lookup_import_media_id_with_fallback(
        &state.pool,
        meta_id,
        data.get("meta_type")
            .and_then(|v| v.as_str())
            .unwrap_or("movie"),
        data.get("title")
            .and_then(|v| v.as_str())
            .unwrap_or(file_name),
        None,
    )
    .await
    .map(i32::from);

    let media_id = media_id.ok_or_else(|| format!("Media not found for meta_id: {meta_id}"))?;

    if let Some(fuid) = file_unique_id {
        let existing: Option<i32> = sqlx::query_scalar(
            "SELECT stream_id FROM telegram_stream WHERE file_unique_id = $1 LIMIT 1",
        )
        .bind(fuid)
        .fetch_optional(&state.pool)
        .await
        .map_err(|e| e.to_string())?;
        if existing.is_some() {
            return Ok(());
        }
    }

    let parsed = parser::parse_title(file_name);
    let (uploader_name, uploader_user_id) = import_helpers::resolve_uploader_identity(
        user_info.contribute_anonymously,
        conv.anonymous_display_name.as_deref(),
        &user_info.username,
        mf_user_id,
    );

    let stream_name = override_str(overrides, "title", None)
        .or_else(|| {
            conv.selected_match
                .as_ref()
                .and_then(|m| m.get("title").and_then(|v| v.as_str()))
        })
        .or(parsed.title.as_deref())
        .unwrap_or(file_name);

    let resolution = override_str(overrides, "resolution", parsed.resolution.as_deref());
    let codec = override_str(overrides, "codec", parsed.codec.as_deref());
    let quality = override_str(overrides, "quality", parsed.quality.as_deref());

    let mut backup_chat_id: Option<String> = None;
    let mut backup_message_id: Option<i64> = None;

    if let Some(backup_channel) = &state.config.telegram_backup_channel_id
        && let Ok(result) = api
            .send_video(backup_channel.as_str(), file_id, Some(file_name))
            .await
    {
        backup_chat_id = Some(backup_channel.clone());
        backup_message_id = result.get("message_id").and_then(|v| v.as_i64());
    }

    let primary_chat_id = backup_chat_id.as_deref().unwrap_or("bot_contribution");
    let primary_message_id = backup_message_id.unwrap_or(0);

    let is_public = data
        .get("is_public")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    let mut base = crate::db::StreamStoreBase::from_parsed(
        stream_name.to_string(),
        "telegram_bot".to_string(),
        &parsed,
    );
    base.resolution = resolution.map(str::to_string);
    base.codec = codec.map(str::to_string);
    base.quality = quality.map(str::to_string);
    base.uploader = Some(uploader_name.clone());
    base.uploader_user_id = uploader_user_id.map(|id| id as i32);
    base.is_public = is_public;

    let tg = crate::db::TelegramStoreInput {
        base,
        chat_id: primary_chat_id.to_string(),
        chat_username: None,
        message_id: primary_message_id as i32,
        file_name: file_name.to_string(),
        size: data.get("file_size").and_then(|v| v.as_i64()).unwrap_or(0),
        mime_type: data
            .get("mime_type")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        file_id: Some(file_id.to_string()),
        file_unique_id: file_unique_id.map(str::to_string),
        backup_chat_id: backup_chat_id.clone(),
        backup_message_id: backup_message_id.map(|id| id as i32),
    };

    let media_type = if conv.media_type.as_deref() == Some("series") {
        crate::db::MediaType::Series
    } else {
        crate::db::MediaType::Movie
    };
    let season = overrides
        .get("season_number")
        .and_then(|v| v.as_i64())
        .map(|v| v as i32);
    let episode = overrides
        .get("episode_number")
        .and_then(|v| v.as_i64())
        .map(|v| v as i32);

    let episode_end = overrides
        .get("episode_end")
        .and_then(|v| v.as_i64())
        .map(|v| v as i32);

    let opts = crate::db::StoreStreamOpts::user_import(crate::db::MediaId(media_id), media_type)
        .with_episode(season, episode, episode_end);

    crate::db::store_telegram_stream(&state.pool, &tg, &opts)
        .await
        .map_err(|e| e.to_string())?;

    if let Some(poster_url) = conv.custom_poster_url.as_deref().filter(|u| !u.is_empty()) {
        let _ = sqlx::query(
            "INSERT INTO media_image \
             (media_id, provider_id, image_type, url, is_primary, display_order) \
             VALUES ($1, 1, 'poster', $2, true, 0) \
             ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING",
        )
        .bind(media_id)
        .bind(poster_url)
        .execute(&state.pool)
        .await;
    }

    if let (Some(bot_token), Some(chat_id)) = (
        state.config.telegram_bot_token.as_deref(),
        state.config.telegram_chat_id.as_deref(),
    ) {
        let http = state.http.clone();
        let notify_file_name = file_name.to_string();
        let notify_meta_id = meta_id.to_string();
        let notify_title = stream_name.to_string();
        let file_size = data.get("file_size").and_then(|v| v.as_i64()).unwrap_or(0);
        let bot_token = bot_token.to_string();
        let chat_id = chat_id.to_string();
        crate::bot::notify_if_enabled(state, async move {
            crate::bot::send_content_received_notification(
                &http,
                &bot_token,
                &chat_id,
                &notify_file_name,
                file_size,
                "stored",
                Some(&notify_meta_id),
                Some(&notify_title),
                None,
            )
            .await;
        });
    }

    Ok(())
}
