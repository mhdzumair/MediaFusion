use fred::clients::Client as RedisClient;
use fred::prelude::KeysInterface;

pub async fn mget(
    client: &RedisClient,
    keys: &[String],
) -> Result<Vec<Option<Vec<u8>>>, Box<dyn std::error::Error + Send + Sync>> {
    if keys.is_empty() {
        return Ok(vec![]);
    }
    let key_refs: Vec<&str> = keys.iter().map(|s| s.as_str()).collect();
    let results: Vec<Option<fred::bytes::Bytes>> = client
        .mget(key_refs)
        .await
        .map_err(|e| format!("Redis MGET: {e}"))?;
    Ok(results
        .into_iter()
        .map(|opt| opt.map(|b| b.to_vec()))
        .collect())
}

pub async fn set_with_ttl(
    client: &RedisClient,
    key: &str,
    blob: Vec<u8>,
    ttl_secs: u64,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use fred::prelude::Expiration;
    client
        .set::<(), _, _>(
            key,
            blob.as_slice(),
            Some(Expiration::EX(ttl_secs as i64)),
            None,
            false,
        )
        .await
        .map_err(|e| format!("Redis SET: {e}").into())
}
