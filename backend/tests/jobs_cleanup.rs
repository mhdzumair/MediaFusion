mod common;

use fred::clients::Client as RedisClient;
use fred::prelude::{ClientLike, KeysInterface, ReconnectPolicy};
use fred::types::config::Config as RedisConfig;
use fred::types::{Expiration, Value as RedisValue};

/// Scan Redis for all keys matching a glob pattern (mirrors the handler).
async fn scan_all_keys(client: &RedisClient, pattern: &str) -> Vec<String> {
    let mut all_keys: Vec<String> = Vec::new();
    let mut cursor = "0".to_string();
    loop {
        let result: Result<RedisValue, _> = client
            .scan_page(cursor.clone(), pattern.to_string(), Some(500), None)
            .await;

        let (next_cursor, keys) = match result {
            Ok(value) => parse_scan_value(value),
            Err(_) => break,
        };
        all_keys.extend(keys);
        if next_cursor == "0" {
            break;
        }
        cursor = next_cursor;
    }
    all_keys
}

fn parse_scan_value(value: RedisValue) -> (String, Vec<String>) {
    if let RedisValue::Array(arr) = value
        && arr.len() == 2
    {
        let cursor = match &arr[0] {
            RedisValue::String(s) => s.to_string(),
            RedisValue::Bytes(b) => String::from_utf8_lossy(b).to_string(),
            RedisValue::Integer(n) => n.to_string(),
            other => format!("{other:?}"),
        };
        let keys = if let RedisValue::Array(key_arr) = &arr[1] {
            key_arr
                .iter()
                .filter_map(|v| match v {
                    RedisValue::String(s) => Some(s.to_string()),
                    RedisValue::Bytes(b) => Some(String::from_utf8_lossy(b).to_string()),
                    _ => None,
                })
                .collect()
        } else {
            Vec::new()
        };
        return (cursor, keys);
    }
    ("0".to_string(), Vec::new())
}

#[tokio::test]
async fn cleanup_deletes_persistent_keys() {
    let redis_url = std::env::var("REDIS_URL").unwrap_or_else(|_| "redis://127.0.0.1:6379".into());

    let config = match RedisConfig::from_url(&redis_url) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("skipping cleanup test: invalid Redis URL: {e}");
            return;
        }
    };

    let client = RedisClient::new(config, None, None, Some(ReconnectPolicy::default()));
    client.connect();
    if client.wait_for_connect().await.is_err() {
        eprintln!("skipping cleanup test: Redis not available");
        return;
    }

    // Set a persistent key (no TTL) that should be cleaned up.
    client
        .set::<(), _, _>(
            "prowlarr:test_stale_key",
            "stale_value",
            None::<Expiration>,
            None,
            false,
        )
        .await
        .unwrap();

    // Verify it exists and has no TTL.
    let ttl: i64 = client.ttl("prowlarr:test_stale_key").await.unwrap();
    assert_eq!(ttl, -1, "key should be persistent before cleanup");

    // Run cleanup logic inline (mirrors delete_stale_keys in handler).
    let keys = scan_all_keys(&client, "prowlarr:*").await;
    for key in &keys {
        let ttl: i64 = client.ttl(key).await.unwrap_or(0);
        if ttl == -1 {
            client.del::<(), _>(key).await.unwrap();
        }
    }

    // Key should be gone.
    let exists: bool = client.exists("prowlarr:test_stale_key").await.unwrap();
    assert!(!exists, "stale key should have been deleted");
}
