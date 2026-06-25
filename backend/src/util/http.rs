use std::time::Duration;

/// Walks the `std::error::Error` source chain and returns the deepest (root) error message.
/// reqwest's top-level Display is always "error sending request for url (...)" which hides
/// the actual cause — use this alongside `{e}` to surface the real error in logs, e.g.:
///   "error sending request ... — caused by: certificate verify failed: UnknownIssuer"
///   "error sending request ... — caused by: connection refused (os error 111)"
pub fn root_cause(e: &reqwest::Error) -> String {
    use std::error::Error;
    let mut source = e.source();
    let mut last = e.to_string();
    while let Some(s) = source {
        last = s.to_string();
        source = s.source();
    }
    last
}

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
/// HTTP/1.1 only: the deployment runs behind a Cloudflare WARP tunnel which silently
/// resets HTTP/2 multiplexed connections mid-flight (GOAWAY/RST_STREAM). Python used
/// requests/aiohttp which never negotiated HTTP/2, so this restores that behaviour.
///
/// `keepalive_secs`: TCP keepalive probe interval. Keeps NAT/conntrack mappings alive
/// through the gost tunnel and lets the OS detect dead sockets before reqwest reuses them.
pub fn build(proxy_url: Option<&str>, keepalive_secs: u64) -> reqwest::Client {
    let ka = Duration::from_secs(keepalive_secs);
    let mut builder = reqwest::Client::builder()
        .user_agent("MediaFusion/1.0 (+https://github.com/mhdzumair/MediaFusion)")
        .http1_only()
        .timeout(Duration::from_secs(30))
        .connect_timeout(Duration::from_secs(10))
        .pool_idle_timeout(Duration::from_secs(20))
        .pool_max_idle_per_host(4)
        .tcp_keepalive(ka)
        .tcp_keepalive_interval(ka)
        .tcp_keepalive_retries(3);
    if let Some(proxy) = proxy_url.filter(|s| !s.is_empty())
        && let Ok(p) = reqwest::Proxy::all(proxy) {
            builder = builder.proxy(p);
        }
    builder.build().expect("HTTP client build failed")
}

/// Outbound HTTP for debrid playback (TorBox createtorrent, mylist, etc.).
/// Sync adds can exceed the 30s scraper timeout; Python used a 15s client but blocked
/// on a Redis lock so only one resolve ran — Rust must match that dedup behavior.
/// HTTP/1.1 only for the same WARP tunnel reason as the general client.
pub fn build_debrid(proxy_url: Option<&str>, keepalive_secs: u64) -> reqwest::Client {
    let ka = Duration::from_secs(keepalive_secs);
    let mut builder = reqwest::Client::builder()
        .user_agent("MediaFusion/1.0 (+https://github.com/mhdzumair/MediaFusion)")
        .http1_only()
        .timeout(Duration::from_secs(90))
        .connect_timeout(Duration::from_secs(15))
        // Keep pool idle timeout well under the server-side HTTP keep-alive timeout (typically
        // 30–60 s on debrid APIs) so we never reuse a connection the server has already closed.
        // 8 s gives a ~22 s safety margin and still benefits from short-burst connection reuse.
        .pool_idle_timeout(Duration::from_secs(8))
        .pool_max_idle_per_host(4)
        .tcp_keepalive(ka)
        .tcp_keepalive_interval(ka)
        .tcp_keepalive_retries(3);
    if let Some(proxy) = proxy_url.filter(|s| !s.is_empty())
        && let Ok(p) = reqwest::Proxy::all(proxy) {
            builder = builder.proxy(p);
        }
    builder.build().expect("debrid HTTP client build failed")
}
