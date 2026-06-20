use fred::clients::Client as RedisClient;
use fred::interfaces::KeysInterface;
use fred::types::Expiration;
use reqwest::Client;
use serde_json::Value;
use tracing::debug;

const IPTV_VALID_CONTENT_TYPES: &[&str] = &[
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "video/mp2t",
    "application/octet-stream",
    "application/dash+xml",
];

const UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36";

fn request_headers(behavior_hints: &Value) -> reqwest::header::HeaderMap {
    let mut headers = reqwest::header::HeaderMap::new();
    if let Some(obj) = behavior_hints
        .get("proxyHeaders")
        .and_then(|v| v.get("request"))
        .and_then(|v| v.as_object())
    {
        for (key, value) in obj {
            if let Some(v) = value.as_str() {
                if let (Ok(name), Ok(val)) = (
                    reqwest::header::HeaderName::from_bytes(key.as_bytes()),
                    reqwest::header::HeaderValue::from_str(v),
                ) {
                    headers.insert(name, val);
                }
            }
        }
    }
    if !headers.contains_key(reqwest::header::USER_AGENT) {
        headers.insert(
            reqwest::header::USER_AGENT,
            reqwest::header::HeaderValue::from_static(UA),
        );
    }
    headers
}

fn expected_content_type(behavior_hints: &Value, response_content_type: &str) -> String {
    behavior_hints
        .get("proxyHeaders")
        .and_then(|v| v.get("response"))
        .and_then(|v| v.get("Content-Type"))
        .and_then(|v| v.as_str())
        .unwrap_or(response_content_type)
        .to_ascii_lowercase()
}

/// HEAD-check a live stream URL for HLS/DASH content types.
pub async fn validate_live_stream_url(
    http: &Client,
    url: &str,
    behavior_hints: &Value,
    validate_url: bool,
) -> bool {
    if validate_url && !super::mediaflow::is_valid_url(url) {
        return false;
    }

    let resp = match http
        .head(url)
        .headers(request_headers(behavior_hints))
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            debug!("live stream HEAD failed [{url}]: {e}");
            return false;
        }
    };

    if !resp.status().is_success() {
        debug!("live stream HEAD status [{url}]: {}", resp.status());
        return false;
    }

    let content_type = resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let content_type = expected_content_type(behavior_hints, content_type);
    IPTV_VALID_CONTENT_TYPES
        .iter()
        .any(|ct| content_type.contains(ct))
}

/// Validate an M3U8/MPD URL with a 5-minute Redis cache (Python parity).
pub async fn validate_m3u8_or_mpd_url_with_cache(
    http: &Client,
    redis: &RedisClient,
    url: &str,
    behavior_hints: &Value,
) -> bool {
    let cache_key = format!("m3u8_url:{url}");
    if let Ok(Some(cached)) = redis.get::<Option<String>, _>(&cache_key).await {
        return cached == "true";
    }

    let is_valid = validate_live_stream_url(http, url, behavior_hints, true).await;
    let _ = redis
        .set::<(), _, _>(
            cache_key,
            if is_valid { "true" } else { "false" },
            Some(Expiration::EX(300)),
            None,
            false,
        )
        .await;
    is_valid
}
