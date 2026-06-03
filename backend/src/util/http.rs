use std::time::Duration;

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
