use fred::clients::Client as RedisClient;
use fred::prelude::{ClientLike, ReconnectPolicy};
use fred::types::config::Config as RedisConfig;

pub async fn build(
    redis_url: &str,
) -> Result<RedisClient, Box<dyn std::error::Error + Send + Sync>> {
    let cfg = RedisConfig::from_url(redis_url)
        .map_err(|e| format!("invalid Redis URL: {e}"))?;
    let client = RedisClient::new(cfg, None, None, Some(ReconnectPolicy::default()));
    client.connect();
    client
        .wait_for_connect()
        .await
        .map_err(|e| format!("Redis connect: {e}"))?;
    Ok(client)
}
