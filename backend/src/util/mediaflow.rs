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
