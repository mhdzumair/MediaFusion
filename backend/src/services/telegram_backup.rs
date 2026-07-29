//! Backup-channel media copy for Telegram streams.
//!
//! Copies media to the configured backup channel as a new message (not a forward).
//! Used during scrape enrichment and admin backup store/restore jobs.

use grammers_client::{Client, message::InputMessage, message::Message};
use grammers_session::types::PeerRef;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;

use crate::{
    bot::BotApi,
    db::telegram::{TelegramStreamBackupRow, update_telegram_stream_backup},
    scrapers::ScrapedTelegramStream,
    services::telegram_peer,
    state::AppState,
    util::telegram_channel_id::{self, ChannelRef},
};

#[derive(Debug, Clone, Default)]
pub struct TelegramStreamEnrichment {
    pub file_unique_id: Option<String>,
    pub document_id: Option<i64>,
    pub file_id: Option<String>,
    pub backup_chat_id: Option<String>,
    pub backup_message_id: Option<i32>,
}

#[derive(Debug, Clone, Default)]
pub struct BackupCopyResult {
    pub backup_chat_id: String,
    pub backup_message_id: i32,
    pub file_id: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct BackupBatchMetrics {
    pub processed: usize,
    pub stored: usize,
    pub restored: usize,
    pub skipped: usize,
    pub errors: usize,
}

async fn resolve_backup_peer_ref(
    client: &Client,
    backup_channel: &str,
    dialog_peers: &HashMap<i64, PeerRef>,
) -> Option<PeerRef> {
    match telegram_channel_id::parse_channel_ref(backup_channel)? {
        ChannelRef::DialogId(dialog_id) => dialog_peers.get(&dialog_id).cloned(),
        ChannelRef::Username(username) => {
            let peer = client.resolve_username(&username).await.ok()??;
            peer.to_ref().await.ok().flatten()
        }
    }
}

fn format_backup_caption(file_name: &str, title: &str) -> String {
    let mut caption = format!("📁 {file_name}\n🎬 {title}");
    if caption.len() > 1024 {
        caption.truncate(1021);
        caption.push_str("...");
    }
    caption
}

pub fn extract_bot_file_id(message: &Value) -> Option<String> {
    for key in ["video", "document", "audio"] {
        if let Some(file_id) = message
            .get(key)
            .and_then(|v| v.get("file_id"))
            .and_then(|v| v.as_str())
        {
            return Some(file_id.to_string());
        }
    }
    None
}

pub fn parse_backup_caption_filename(caption: &str) -> Option<String> {
    caption.lines().find_map(|line| {
        line.strip_prefix("📁 ")
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
    })
}

fn bot_api_chat_id(channel: &str) -> Option<serde_json::Value> {
    match telegram_channel_id::parse_channel_ref(channel)? {
        ChannelRef::DialogId(id) => Some(serde_json::json!(id)),
        ChannelRef::Username(username) => Some(serde_json::json!(format!("@{username}"))),
    }
}

async fn copy_message_to_backup_via_bot(
    api: &BotApi,
    backup_channel: &str,
    source_chat_id: &str,
    message_id: i32,
    file_name: &str,
    title: &str,
) -> Option<BackupCopyResult> {
    let backup_chat_id = bot_api_chat_id(backup_channel)?;
    let source_chat_id = bot_api_chat_id(source_chat_id)?;
    let caption = format_backup_caption(file_name, title);

    let copied = api
        .copy_message_with_caption_json(
            backup_chat_id.clone(),
            source_chat_id,
            i64::from(message_id),
            Some(&caption),
        )
        .await
        .ok()?;

    let backup_message_id = copied
        .get("message_id")
        .and_then(|v| v.as_i64())
        .map(|id| id as i32)?;

    Some(BackupCopyResult {
        backup_chat_id: backup_channel.to_string(),
        backup_message_id,
        file_id: extract_bot_file_id(&copied),
    })
}

async fn copy_message_to_backup(
    client: &Client,
    dialog_peers: &HashMap<i64, PeerRef>,
    backup_channel: &str,
    source_chat_id: &str,
    message_id: i32,
    file_name: &str,
    title: &str,
) -> Option<BackupCopyResult> {
    let (_, source_peer_ref) =
        telegram_peer::resolve_channel_peer(client, source_chat_id, dialog_peers).await?;

    let messages = client
        .get_messages_by_id(source_peer_ref, &[message_id])
        .await
        .ok()?;
    let message = match messages.into_iter().next() {
        Some(Some(msg)) => msg,
        _ => return None,
    };
    let media = message.media()?;

    let backup_peer_ref = resolve_backup_peer_ref(client, backup_channel, dialog_peers).await?;
    let caption = format_backup_caption(file_name, title);
    let input = InputMessage::new().text(&caption).copy_media(&media);
    let sent = client.send_message(backup_peer_ref, input).await.ok()?;

    Some(BackupCopyResult {
        backup_chat_id: backup_channel.to_string(),
        backup_message_id: sent.id(),
        file_id: None,
    })
}

async fn capture_file_id_from_backup(
    api: &BotApi,
    backup_chat_id: &str,
    backup_message_id: i32,
) -> Option<String> {
    let chat_id = backup_chat_id.parse::<i64>().ok()?;
    // Temporary in-channel bot copy → extract file_id → delete duplicate.
    // Keeps admin/user chats clean; only requires bot access to the backup channel.
    let copied = api
        .copy_message(chat_id, chat_id, i64::from(backup_message_id))
        .await
        .ok()?;
    let file_id = extract_bot_file_id(&copied);
    if let Some(message_id) = copied.get("message_id").and_then(|v| v.as_i64())
        && let Err(e) = api.delete_message(chat_id, message_id).await
    {
        tracing::warn!(
            "telegram backup: failed to delete temporary file_id probe message {message_id} in {backup_chat_id}: {e}"
        );
    }
    file_id
}

fn document_unique_id(message: &Message) -> Option<String> {
    use grammers_client::media::Media;
    match message.media()? {
        Media::Document(doc) => Some(doc.id().to_string()),
        _ => None,
    }
}

pub async fn enrich_scraped_stream(
    state: &AppState,
    client: &Client,
    _channel: &str,
    stream: &ScrapedTelegramStream,
    dialog_peers: &HashMap<i64, PeerRef>,
    title: &str,
) -> TelegramStreamEnrichment {
    let mut enrichment = TelegramStreamEnrichment {
        file_unique_id: stream.file_unique_id.clone(),
        document_id: stream.document_id,
        ..Default::default()
    };

    let Some(backup_channel) = state
        .config
        .telegram_backup_channel_id
        .as_deref()
        .filter(|s| !s.is_empty())
    else {
        return enrichment;
    };

    let Some(result) = copy_message_to_backup(
        client,
        dialog_peers,
        backup_channel,
        &stream.chat_id.to_string(),
        stream.message_id,
        &stream.file_name,
        title,
    )
    .await
    else {
        tracing::warn!(
            "telegram backup: failed to copy media for {}:{}",
            stream.chat_id,
            stream.message_id
        );
        return enrichment;
    };

    enrichment.backup_chat_id = Some(result.backup_chat_id.clone());
    enrichment.backup_message_id = Some(result.backup_message_id);

    if let Ok(api) = BotApi::from_state(state) {
        enrichment.file_id =
            capture_file_id_from_backup(&api, &result.backup_chat_id, result.backup_message_id)
                .await;
    }

    enrichment
}

pub async fn store_stream_to_backup(
    state: &AppState,
    user_client: Option<&Client>,
    user_dialog_peers: Option<&HashMap<i64, PeerRef>>,
    row: &TelegramStreamBackupRow,
    capture_file_id: bool,
) -> Result<BackupCopyResult, String> {
    let backup_channel = state
        .config
        .telegram_backup_channel_id
        .as_deref()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| "TELEGRAM_BACKUP_CHANNEL_ID is not configured".to_string())?;

    if telegram_channel_id::parse_channel_ref(&row.chat_id).is_none() {
        return Err(format!(
            "invalid chat_id `{}` — legacy row needs re-scrape or deletion",
            row.chat_id
        ));
    }

    let title = if row.stream_name.is_empty() {
        row.file_name.as_str()
    } else {
        row.stream_name.as_str()
    };

    let mut result = if row.chat_id == backup_channel {
        BackupCopyResult {
            backup_chat_id: backup_channel.to_string(),
            backup_message_id: row.message_id,
            file_id: row.file_id.clone(),
        }
    } else if let Ok(api) = BotApi::from_state(state)
        && let Some(copied) = copy_message_to_backup_via_bot(
            &api,
            backup_channel,
            &row.chat_id,
            row.message_id,
            &row.file_name,
            title,
        )
        .await
    {
        copied
    } else if let (Some(client), Some(dialog_peers)) = (user_client, user_dialog_peers)
        && let Some(copied) = copy_message_to_backup(
            client,
            dialog_peers,
            backup_channel,
            &row.chat_id,
            row.message_id,
            &row.file_name,
            title,
        )
        .await
    {
        copied
    } else {
        return Err(format!(
            "failed to copy stream {} from {}:{} — bot must be admin in source and backup channels, or a user scraping session with channel access is required",
            row.id, row.chat_id, row.message_id
        ));
    };

    if capture_file_id && let Ok(api) = BotApi::from_state(state) {
        result.file_id =
            capture_file_id_from_backup(&api, &result.backup_chat_id, result.backup_message_id)
                .await;
    }

    update_telegram_stream_backup(
        &state.pool,
        row.id,
        &result.backup_chat_id,
        result.backup_message_id,
        result.file_id.as_deref(),
    )
    .await
    .map_err(|e| e.to_string())?;

    Ok(result)
}

pub async fn restore_stream_from_backup_message(
    state: &AppState,
    backup_channel: &str,
    message: &Message,
    capture_file_id: bool,
) -> Result<Option<i32>, String> {
    let media = message
        .media()
        .ok_or_else(|| "backup message has no media".to_string())?;
    let _ = media;

    let caption = message.text();
    let caption_filename = parse_backup_caption_filename(caption);
    let document_unique = document_unique_id(message);
    let media_filename = message.media().and_then(|media| match media {
        grammers_client::media::Media::Document(doc) => doc.name().map(str::to_string),
        _ => None,
    });
    let lookup_filename = caption_filename.or(media_filename);

    let row = crate::db::telegram::find_stream_for_restore(
        &state.pool,
        document_unique.as_deref(),
        lookup_filename.as_deref(),
    )
    .await
    .ok_or_else(|| "no matching telegram stream in database".to_string())?;

    let backup_message_id = message.id();
    let mut file_id = row.file_id.clone();

    if capture_file_id && let Ok(api) = BotApi::from_state(state) {
        file_id = capture_file_id_from_backup(&api, backup_channel, backup_message_id)
            .await
            .or(file_id);
    }

    update_telegram_stream_backup(
        &state.pool,
        row.id,
        backup_channel,
        backup_message_id,
        file_id.as_deref(),
    )
    .await
    .map_err(|e| e.to_string())?;

    Ok(Some(row.id))
}

pub async fn resolve_bot_mtproto_client(state: &AppState) -> Result<Arc<Client>, String> {
    state
        .telegram_clients
        .get_bot_client()
        .await
        .ok_or_else(|| {
            "Telegram bot MTProto client unavailable — configure TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID, and TELEGRAM_API_HASH".to_string()
        })
}

pub async fn resolve_session_user_id(
    pool: &sqlx::PgPool,
    preferred_user_id: Option<crate::db::types::UserId>,
) -> Option<crate::db::types::UserId> {
    if let Some(user_id) = preferred_user_id {
        if crate::db::user_telegram_session::has_session(pool, user_id).await {
            return Some(user_id);
        }
        return None;
    }

    for user_id in crate::db::user_telegram_session::list_session_user_ids(pool).await {
        if crate::db::user_telegram_session::has_session(pool, user_id).await {
            return Some(user_id);
        }
    }

    None
}
