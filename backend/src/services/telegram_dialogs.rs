//! List channels, groups, and bot chats from a user's connected Telegram MTProto session.

use grammers_client::peer::Peer;
use grammers_session::types::PeerRef;
use std::collections::HashMap;
use std::sync::Arc;

use std::time::Duration;

use crate::{
    db::types::UserId,
    scrapers::telegram_clients::{TelegramClientPool, is_auth_key_duplicated},
    services::telegram_peer,
    util::telegram_channel_id,
};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ScrapableDialog {
    pub id: String,
    pub name: String,
    pub kind: String,
    /// True when the user session can access this dialog (channels, joined groups, and bots).
    pub scrapable: bool,
    pub is_public: bool,
    pub has_photo: bool,
}

fn dialog_display_name(name: &str, fallback: &str) -> String {
    if name.trim().is_empty() {
        fallback.to_string()
    } else {
        name.to_string()
    }
}

pub async fn list_scrapable_dialogs(
    pool: &sqlx::PgPool,
    clients: &TelegramClientPool,
    user_id: UserId,
    limit: usize,
) -> Result<Vec<ScrapableDialog>, String> {
    for attempt in 0..2 {
        let result = clients
            .with_user_client(pool, user_id, |client| {
                fetch_scrapable_dialogs(client, user_id, limit)
            })
            .await;

        match result {
            Ok(Ok(dialogs)) => return Ok(dialogs),
            Ok(Err(err)) if attempt == 0 && is_auth_key_duplicated(&err) => {
                tracing::warn!(
                    "telegram: AUTH_KEY_DUPLICATED for user {} — recycling session lease",
                    user_id.0
                );
                clients.invalidate(user_id).await;
                telegram_peer::invalidate_dialog_peer_cache(user_id).await;
                tokio::time::sleep(Duration::from_secs(2)).await;
            }
            Ok(Err(err)) => return Err(err),
            Err(err) => return Err(err),
        }
    }

    Err("Telegram session is busy — wait a moment and try again".into())
}

async fn fetch_scrapable_dialogs(
    client: Arc<grammers_client::Client>,
    user_id: UserId,
    limit: usize,
) -> Result<Vec<ScrapableDialog>, String> {
    let mut iter = client.iter_dialogs();
    let mut results = Vec::new();
    let mut peer_map = HashMap::<i64, PeerRef>::new();

    while results.len() < limit {
        let next = iter
            .next()
            .await
            .map_err(|e| format!("list dialogs: {e}"))?;
        let Some(dialog) = next else {
            break;
        };

        let Some(dialog_id) = dialog.peer.id().bot_api_dialog_id() else {
            continue;
        };
        peer_map.insert(dialog_id, dialog.peer_ref());

        match &dialog.peer {
            Peer::Channel(channel) => {
                let username = channel.username().map(|u| format!("@{u}"));
                let is_public = username.is_some();
                let has_photo = channel.photo().is_some();
                let name = channel.title().to_string();
                results.push(ScrapableDialog {
                    scrapable: true,
                    is_public,
                    has_photo,
                    id: username.unwrap_or_else(|| {
                        telegram_channel_id::format_dialog_id(
                            channel.id().bot_api_dialog_id_unchecked(),
                        )
                    }),
                    name,
                    kind: "channel".to_string(),
                });
            }
            Peer::Group(group) => {
                let username = group.username().map(|u| format!("@{u}"));
                let is_public = username.is_some();
                let has_photo = group.photo().is_some();
                let name = group.title().unwrap_or("Unnamed group").to_string();
                results.push(ScrapableDialog {
                    scrapable: true,
                    is_public,
                    has_photo,
                    id: username.unwrap_or_else(|| {
                        telegram_channel_id::format_dialog_id(
                            group.id().bot_api_dialog_id_unchecked(),
                        )
                    }),
                    name,
                    kind: "group".to_string(),
                });
            }
            Peer::User(user) if user.is_bot() && !user.deleted() => {
                let username = user.username().map(|u| format!("@{u}"));
                let is_public = username.is_some();
                let has_photo = user.photo().is_some();
                let fallback = username.clone().unwrap_or_else(|| "Bot".to_string());
                let name = dialog_display_name(&user.full_name(), &fallback);
                results.push(ScrapableDialog {
                    scrapable: true,
                    is_public,
                    has_photo,
                    id: username.unwrap_or_else(|| {
                        telegram_channel_id::format_dialog_id(
                            user.id().bot_api_dialog_id_unchecked(),
                        )
                    }),
                    name,
                    kind: "bot".to_string(),
                });
            }
            _ => {}
        }
    }

    telegram_peer::store_dialog_peer_map(user_id, peer_map).await;

    Ok(results)
}
