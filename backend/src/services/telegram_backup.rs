//! Backup-channel forwarding and Bot API file_id enrichment for scraped Telegram streams.

use grammers_client::Client;
use grammers_session::types::PeerRef;
use serde_json::Value;
use std::collections::HashMap;

use crate::{
    bot::BotApi,
    scrapers::ScrapedTelegramStream,
    services::telegram_peer,
    state::AppState,
    util::telegram_channel_id::{self, ChannelRef},
};

#[derive(Debug, Clone, Default)]
pub struct TelegramStreamEnrichment {
    pub file_id: Option<String>,
    pub file_unique_id: Option<String>,
    pub document_id: Option<i64>,
    pub backup_chat_id: Option<String>,
    pub backup_message_id: Option<i32>,
    pub primary_chat_id: Option<String>,
    pub primary_message_id: Option<i32>,
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

fn extract_bot_file_id(message: &Value) -> Option<String> {
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

pub async fn enrich_scraped_stream(
    state: &AppState,
    client: &Client,
    channel: &str,
    stream: &ScrapedTelegramStream,
    dialog_peers: &HashMap<i64, PeerRef>,
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

    let Ok(api) = BotApi::from_state(state) else {
        return enrichment;
    };

    let Some((_, source_peer_ref)) =
        telegram_peer::resolve_channel_peer(client, channel, dialog_peers).await
    else {
        return enrichment;
    };

    let Ok(messages) = client
        .get_messages_by_id(source_peer_ref.clone(), &[stream.message_id])
        .await
    else {
        return enrichment;
    };
    let Some(Some(message)) = messages.into_iter().next() else {
        return enrichment;
    };

    let Some(backup_peer_ref) = resolve_backup_peer_ref(client, backup_channel, dialog_peers).await
    else {
        tracing::warn!("telegram backup: backup channel {backup_channel} is not accessible");
        return enrichment;
    };

    let Ok(forwarded) = message.forward_to(backup_peer_ref).await else {
        tracing::warn!(
            "telegram backup: MTProto forward failed for {}:{}",
            stream.chat_id,
            stream.message_id
        );
        return enrichment;
    };

    let backup_message_id = forwarded.id();
    enrichment.backup_chat_id = Some(backup_channel.to_string());
    enrichment.backup_message_id = Some(backup_message_id);
    enrichment.primary_chat_id = enrichment.backup_chat_id.clone();
    enrichment.primary_message_id = enrichment.backup_message_id;

    let Some(capture_chat_id) = state
        .config
        .telegram_chat_id
        .as_deref()
        .and_then(|s| s.parse::<i64>().ok())
    else {
        return enrichment;
    };

    let Some(from_chat_id) = backup_channel.parse::<i64>().ok() else {
        return enrichment;
    };

    match api
        .forward_message(capture_chat_id, from_chat_id, i64::from(backup_message_id))
        .await
    {
        Ok(result) => {
            enrichment.file_id = extract_bot_file_id(&result);
        }
        Err(e) => {
            tracing::warn!("telegram backup: bot forward for file_id failed: {e}");
        }
    }

    enrichment
}
