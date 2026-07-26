use std::collections::BTreeMap;

use url::Url;

/// Build a MediaFlow proxy URL with plain query parameters (no token encryption).
///
/// Mirrors Python `encode_mediaflow_proxy_url`.
pub fn encode_mediaflow_proxy_url(
    mediaflow_proxy_url: &str,
    endpoint: &str,
    destination_url: Option<&str>,
    query_params: BTreeMap<String, String>,
    request_headers: Option<&serde_json::Map<String, serde_json::Value>>,
    response_headers: Option<&serde_json::Map<String, serde_json::Value>>,
) -> Result<String, String> {
    let base = mediaflow_proxy_url.trim_end_matches('/');
    let endpoint = endpoint.trim_start_matches('/');
    let mut params = query_params;

    if let Some(dest) = destination_url.filter(|s| !s.is_empty()) {
        params.insert("d".into(), dest.to_string());
    }

    if let Some(headers) = request_headers {
        for (key, value) in headers {
            if let Some(v) = value.as_str() {
                params.insert(format!("h_{key}"), v.to_string());
            }
        }
    }
    if let Some(headers) = response_headers {
        for (key, value) in headers {
            if let Some(v) = value.as_str() {
                params.insert(format!("r_{key}"), v.to_string());
            }
        }
    }

    let base_url = format!("{base}/{endpoint}");

    let query = params
        .into_iter()
        .map(|(k, v)| format!("{}={}", k, urlencoding::encode(&v)))
        .collect::<Vec<_>>()
        .join("&");

    Ok(format!("{base_url}?{query}"))
}

/// Build a MediaFlow proxy URL for AceStream MPEG-TS playback.
pub fn encode_mediaflow_acestream_url(
    mediaflow_proxy_url: &str,
    content_id: Option<&str>,
    info_hash: Option<&str>,
    api_password: Option<&str>,
) -> Option<String> {
    let content_id = content_id.filter(|s| !s.is_empty());
    let info_hash = info_hash.filter(|s| !s.is_empty());
    if content_id.is_none() && info_hash.is_none() {
        return None;
    }

    let mut params = BTreeMap::new();
    if let Some(id) = content_id {
        params.insert("id".into(), id.to_string());
    } else if let Some(ih) = info_hash {
        params.insert("infohash".into(), ih.to_string());
    }
    if let Some(ap) = api_password.filter(|s| !s.is_empty()) {
        params.insert("api_password".into(), ap.to_string());
    }

    encode_mediaflow_proxy_url(
        mediaflow_proxy_url,
        "/proxy/acestream/stream",
        None,
        params,
        None,
        None,
    )
    .ok()
}

/// Returns true when `url` has a usable scheme and host.
pub fn is_valid_url(url: &str) -> bool {
    Url::parse(url)
        .ok()
        .is_some_and(|parsed| parsed.scheme().starts_with("http") && parsed.host().is_some())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_valid_url_requires_http_scheme_and_host() {
        assert!(is_valid_url("https://example.com/stream.m3u8"));
        assert!(!is_valid_url("not-a-url"));
        assert!(!is_valid_url(""));
    }

    #[test]
    fn encode_acestream_url_prefers_content_id() {
        let url = encode_mediaflow_acestream_url(
            "https://proxy.example.com",
            Some("0123456789abcdef0123456789abcdef01234567"),
            Some("fedcba9876543210fedcba9876543210fedcba98"),
            Some("secret"),
        )
        .unwrap();
        assert!(url.contains("/proxy/acestream/stream?"));
        assert!(url.contains("id=0123456789abcdef0123456789abcdef01234567"));
        assert!(url.contains("api_password=secret"));
        assert!(!url.contains("infohash="));
    }

    #[test]
    fn encode_acestream_url_falls_back_to_info_hash() {
        let url = encode_mediaflow_acestream_url(
            "https://proxy.example.com",
            None,
            Some("fedcba9876543210fedcba9876543210fedcba98"),
            None,
        )
        .unwrap();
        assert!(url.contains("infohash=fedcba9876543210fedcba9876543210fedcba98"));
    }

    #[test]
    fn encode_plain_proxy_url_includes_destination() {
        let url = encode_mediaflow_proxy_url(
            "https://proxy.example.com",
            "/proxy/hls/manifest.m3u8",
            Some("https://cdn.example.com/live.m3u8"),
            BTreeMap::from([("api_password".into(), "secret".into())]),
            None,
            None,
        )
        .unwrap();
        assert!(url.contains("/proxy/hls/manifest.m3u8?"));
        assert!(url.contains("api_password=secret"));
        assert!(url.contains("d=https"));
    }
}
