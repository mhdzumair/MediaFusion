//! Forwarded video storage and backup-channel copy.

use serde_json::Value;

use crate::db::StreamType;
use crate::{
    parser,
    routes::content::import_helpers::{self, UserInfo},
    state::AppState,
};

use super::{
    api::BotApi,
    model::ConversationState,
};

fn override_str<'a>(overrides: &'a Value, field: &str, fallback: Option<&'a str>) -> Option<&'a str> {
    overrides
        .get(field)
        .and_then(|v| v.as_str())
        .or(fallback)
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
        data.get("meta_type").and_then(|v| v.as_str()).unwrap_or("movie"),
        data.get("title").and_then(|v| v.as_str()).unwrap_or(file_name),
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

    let resolution =
        override_str(overrides, "resolution", parsed.resolution.as_deref());
    let codec = override_str(overrides, "codec", parsed.codec.as_deref());
    let quality = override_str(overrides, "quality", parsed.quality.as_deref());

    let mut backup_chat_id: Option<String> = None;
    let mut backup_message_id: Option<i64> = None;

    if let Some(backup_channel) = &state.config.telegram_backup_channel_id {
        if let Ok(result) = api.send_video(backup_channel.as_str(), file_id, Some(file_name)).await {
            backup_chat_id = Some(backup_channel.clone());
            backup_message_id = result
                .get("message_id")
                .and_then(|v| v.as_i64());
        }
    }

    let primary_chat_id = backup_chat_id.as_deref().unwrap_or("bot_contribution");
    let primary_message_id = backup_message_id.unwrap_or(0);

    let is_public = data.get("is_public").and_then(|v| v.as_bool()).unwrap_or(false);

    let stream_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO stream(
               stream_type, name, source,
               resolution, codec, quality,
               is_proper, is_repack, is_remastered, is_upscaled, is_extended,
               is_complete, is_dubbed, is_subbed, release_group,
               is_active, is_blocked, is_public, playback_count,
               uploader, uploader_user_id, created_at
           ) VALUES(
               $1, $2, $3,
               $4, $5, $6,
               $7, $8, $9, $10, $11,
               $12, $13, $14, $15,
               true, false, $16, 0,
               $17, $18, NOW()
           ) RETURNING id"#,
    )
    .bind(StreamType::Telegram)
    .bind(stream_name)
    .bind("telegram_bot")
    .bind(resolution)
    .bind(codec)
    .bind(quality)
    .bind(parsed.is_proper)
    .bind(parsed.is_repack)
    .bind(parsed.is_remastered)
    .bind(parsed.is_upscaled)
    .bind(parsed.is_extended)
    .bind(parsed.is_complete)
    .bind(parsed.is_dubbed)
    .bind(parsed.is_subbed)
    .bind(parsed.release_group.as_deref())
    .bind(is_public)
    .bind(&uploader_name)
    .bind(uploader_user_id)
    .fetch_one(&state.pool)
    .await
    .map_err(|e| e.to_string())?;

    sqlx::query(
        r#"INSERT INTO telegram_stream
           (stream_id, chat_id, message_id, file_id, file_unique_id, file_name, mime_type, size,
            backup_chat_id, backup_message_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)"#,
    )
    .bind(stream_id)
    .bind(primary_chat_id)
    .bind(primary_message_id)
    .bind(file_id)
    .bind(file_unique_id)
    .bind(file_name)
    .bind(data.get("mime_type").and_then(|v| v.as_str()))
    .bind(data.get("file_size").and_then(|v| v.as_i64()))
    .bind(backup_chat_id.as_deref())
    .bind(backup_message_id)
    .execute(&state.pool)
    .await
    .map_err(|e| e.to_string())?;

    let _ = import_helpers::link_stream_to_media(&state.pool, stream_id, crate::db::MediaId(media_id))
        .await;

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

    Ok(())
}
