use std::time::Duration;

/// Shared reqwest client for all outbound HTTP (scrapers, RPDB, etc.).
pub fn build() -> reqwest::Client {
    reqwest::Client::builder()
        .user_agent("MediaFusion/1.0 (+https://github.com/mhdzumair/MediaFusion)")
        .timeout(Duration::from_secs(30))
        .connect_timeout(Duration::from_secs(10))
        .tcp_keepalive(Duration::from_secs(60))
        .build()
        .expect("HTTP client build failed")
}
