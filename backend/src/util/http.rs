use std::time::Duration;

/// Returns `true` for transport-level failures that are safe to retry:
/// connection errors, timeouts, and low-level request build/send failures.
/// HTTP 4xx/5xx responses are *not* transport errors — only `Err(reqwest::Error)` arms
/// (before `.status()` is read) need this check.
pub fn is_transport_error(e: &reqwest::Error) -> bool {
    e.is_timeout() || e.is_connect() || e.is_request()
}

/// Short label for a reqwest error useful as a structured log field.
/// Distinguishes the most actionable categories without requiring the caller to read the
/// full error string.
pub fn transport_error_kind(e: &reqwest::Error) -> &'static str {
    if e.is_timeout() {
        "timeout"
    } else if e.is_connect() {
        "connect"
    } else if e.is_request() {
        "request"
    } else if e.is_body() {
        "body"
    } else if e.is_decode() {
        "decode"
    } else if e.is_redirect() {
        "redirect"
    } else if e.is_builder() {
        "builder"
    } else if e.is_upgrade() {
        "upgrade"
    } else if e.is_status() {
        "status"
    } else {
        "other"
    }
}

/// Shared reqwest client for all outbound HTTP (scrapers, RPDB, etc.).
pub fn build(proxy_url: Option<&str>) -> reqwest::Client {
    let mut builder = reqwest::Client::builder()
        .user_agent("MediaFusion/1.0 (+https://github.com/mhdzumair/MediaFusion)")
        .timeout(Duration::from_secs(30))
        .connect_timeout(Duration::from_secs(10))
        .tcp_keepalive(Duration::from_secs(60))
        .pool_idle_timeout(Duration::from_secs(90))
        .pool_max_idle_per_host(4);
    if let Some(proxy) = proxy_url.filter(|s| !s.is_empty()) {
        if let Ok(p) = reqwest::Proxy::all(proxy) {
            builder = builder.proxy(p);
        }
    }
    builder.build().expect("HTTP client build failed")
}

/// Outbound HTTP for debrid playback (TorBox createtorrent, mylist, etc.).
/// Sync adds can exceed the 30s scraper timeout; Python used a 15s client but blocked
/// on a Redis lock so only one resolve ran — Rust must match that dedup behavior.
pub fn build_debrid(proxy_url: Option<&str>) -> reqwest::Client {
    let mut builder = reqwest::Client::builder()
        .user_agent("MediaFusion/1.0 (+https://github.com/mhdzumair/MediaFusion)")
        .timeout(Duration::from_secs(90))
        .connect_timeout(Duration::from_secs(15))
        .tcp_keepalive(Duration::from_secs(60))
        .pool_idle_timeout(Duration::from_secs(90))
        .pool_max_idle_per_host(4);
    if let Some(proxy) = proxy_url.filter(|s| !s.is_empty()) {
        if let Ok(p) = reqwest::Proxy::all(proxy) {
            builder = builder.proxy(p);
        }
    }
    builder.build().expect("debrid HTTP client build failed")
}
