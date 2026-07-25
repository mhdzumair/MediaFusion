//! List channels/groups from a user's connected Telegram MTProto session.

use grammers_client::peer::Peer;
use grammers_session::types::PeerRef;
use std::collections::HashMap;

use crate::{
    db::types::UserId, scrapers::telegram_clients::TelegramClientPool, services::telegram_peer,
    util::telegram_channel_id,
};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ScrapableDialog {
    pub id: String,
    pub name: String,
    pub kind: String,
    /// True when the user session can access this dialog (channels and joined groups).
    pub scrapable: bool,
    pub is_public: bool,
    pub has_photo: bool,
}

pub async fn list_scrapable_dialogs(
    pool: &sqlx::PgPool,
    clients: &TelegramClientPool,
    user_id: UserId,
    limit: usize,
) -> Result<Vec<ScrapableDialog>, String> {
    let Some(client) = clients.get_client(pool, user_id).await else {
        return Err("Telegram scraping session is not connected".into());
    };

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
            _ => {}
        }
    }

    telegram_peer::store_dialog_peer_map(user_id, peer_map).await;

    Ok(results)
}
