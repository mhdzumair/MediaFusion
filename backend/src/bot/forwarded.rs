//! Forwarded video storage and backup-channel copy.

use serde_json::Value;

use crate::{
    parser,
    routes::content::import_helpers::{self, UserInfo},
    state::AppState,
};

use super::{
    api::BotApi,
    model::ConversationState,
};

pub async fn store_forwarded_video(
    state: &AppState,
    api: &BotApi,
    conv: &ConversationState,
    mf_user_id: i64,
    user_info: &UserInfo,
    data: &Value,
) -> Result<(), String> {
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
    .map(|id| id as i32);

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
        None,
        &user_info.username,
        mf_user_id,
    );

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
               'TELEGRAM'::streamtype, $1, $2,
               $3, $4, $5,
               $6, $7, $8, $9, $10,
               $11, $12, $13, $14,
               true, false, $15, 0,
               $16, $17, NOW()
           ) RETURNING id"#,
    )
    .bind(parsed.title.as_deref().unwrap_or(file_name))
    .bind("telegram_bot")
    .bind(parsed.resolution.as_deref())
    .bind(parsed.codec.as_deref())
    .bind(parsed.quality.as_deref())
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

    let _ = import_helpers::link_stream_to_media(&state.pool, stream_id, media_id).await;

    Ok(())
}

/// Handle auto-ingest of forwarded videos (no wizard) when user sends/forwards video.
pub async fn try_auto_ingest_forwarded(
    state: &AppState,
    api: &BotApi,
    user_id: i64,
    chat_id: i64,
    raw_input: Value,
) -> bool {
    let _ = (state, api, user_id, chat_id, raw_input);
    false
}
