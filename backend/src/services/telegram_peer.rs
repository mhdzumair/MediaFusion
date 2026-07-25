//! Resolve Telegram peers, cache dialog lists, and download channel photos.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use grammers_client::Client;
use grammers_session::types::PeerRef;
use moka::future::Cache;

use crate::{
    db::types::UserId,
    util::telegram_channel_id::{self, ChannelRef},
};

static DIALOG_PEER_CACHE: std::sync::OnceLock<Cache<UserId, Arc<HashMap<i64, PeerRef>>>> =
    std::sync::OnceLock::new();

fn dialog_peer_cache() -> &'static Cache<UserId, Arc<HashMap<i64, PeerRef>>> {
    DIALOG_PEER_CACHE.get_or_init(|| {
        Cache::builder()
            .max_capacity(128)
            .time_to_live(Duration::from_secs(5 * 60))
            .build()
    })
}

pub async fn invalidate_dialog_peer_cache(user_id: UserId) {
    dialog_peer_cache().invalidate(&user_id).await;
}

pub async fn load_dialog_peer_map(client: &Client) -> HashMap<i64, PeerRef> {
    let mut map = HashMap::new();
    let mut iter = client.iter_dialogs();
    loop {
        match iter.next().await {
            Ok(Some(dialog)) => {
                let Some(dialog_id) = dialog.peer.id().bot_api_dialog_id() else {
                    continue;
                };
                map.insert(dialog_id, dialog.peer_ref());
            }
            Ok(None) => break,
            Err(e) => {
                tracing::warn!("telegram: load dialog peers: {e}");
                break;
            }
        }
    }
    map
}

pub async fn store_dialog_peer_map(user_id: UserId, map: HashMap<i64, PeerRef>) {
    dialog_peer_cache().insert(user_id, Arc::new(map)).await;
}

pub async fn cached_dialog_peer_map(user_id: UserId, client: &Client) -> HashMap<i64, PeerRef> {
    if let Some(cached) = dialog_peer_cache().get(&user_id).await {
        return (*cached).clone();
    }
    let map = load_dialog_peer_map(client).await;
    dialog_peer_cache()
        .insert(user_id, Arc::new(map.clone()))
        .await;
    map
}

pub async fn resolve_channel_peer(
    client: &Client,
    channel_id: &str,
    dialog_peers: &HashMap<i64, PeerRef>,
) -> Option<(grammers_client::peer::Peer, PeerRef)> {
    let channel_ref = telegram_channel_id::parse_channel_ref(channel_id)?;
    resolve_channel_ref(client, channel_ref, dialog_peers).await
}

pub async fn resolve_channel_ref(
    client: &Client,
    channel_ref: ChannelRef,
    dialog_peers: &HashMap<i64, PeerRef>,
) -> Option<(grammers_client::peer::Peer, PeerRef)> {
    match channel_ref {
        ChannelRef::Username(username) => {
            let peer = client.resolve_username(&username).await.ok()??;
            let peer_ref = peer.to_ref().await.ok().flatten()?;
            Some((peer, peer_ref))
        }
        ChannelRef::DialogId(dialog_id) => {
            let peer_ref = dialog_peers.get(&dialog_id)?.clone();
            let peer = client.resolve_peer(peer_ref.clone()).await.ok()?;
            Some((peer, peer_ref))
        }
    }
}

pub async fn download_channel_photo(
    client: &Client,
    channel_id: &str,
    dialog_peers: &HashMap<i64, PeerRef>,
) -> Option<Vec<u8>> {
    let (peer, _) = resolve_channel_peer(client, channel_id, dialog_peers).await?;
    let photo = peer.photo(false).await.ok().flatten()?;
    let mut iter = client.iter_download(&photo);
    let mut bytes = Vec::new();
    loop {
        match iter.next().await {
            Ok(Some(chunk)) => bytes.extend_from_slice(&chunk),
            Ok(None) => break,
            Err(e) => {
                tracing::warn!("telegram: download channel photo {channel_id}: {e}");
                return None;
            }
        }
    }
    (!bytes.is_empty()).then_some(bytes)
}
