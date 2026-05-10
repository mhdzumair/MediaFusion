/// Redis caching for resolved usenet playback URLs.
///
/// Key: `usenet_provider_` + hex(SHA256(`{secret}_{nzb_guid}_{season}_{episode}`))
/// TTL: 1 hour — long enough to survive a Stremio retry loop, short enough
///      to pick up renewed TorBox/Debrider links after expiry.
use fred::prelude::{Expiration, KeysInterface};
use sha2::{Digest, Sha256};

const KEY_PREFIX: &str = "usenet_provider_";
pub const TTL: i64 = 3600;

pub fn cache_key(secret: &str, nzb_guid: &str, season: i32, episode: i32) -> String {
    let raw = format!("{secret}_{nzb_guid}_{season}_{episode}");
    let hex: String = Sha256::digest(raw.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    format!("{KEY_PREFIX}{hex}")
}

pub async fn get(redis: &fred::clients::Client, key: &str) -> Option<String> {
    redis.get::<Option<String>, _>(key).await.ok().flatten()
}

pub async fn set(redis: &fred::clients::Client, key: &str, url: &str) {
    let _ = redis
        .set::<(), _, _>(key, url, Some(Expiration::EX(TTL)), None, false)
        .await;
}
