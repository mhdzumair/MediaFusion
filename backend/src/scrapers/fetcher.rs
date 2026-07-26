/// HTTP fetch abstraction for public indexer scrapers.
///
/// Modes:
///   - Plain HTTP (browser UA, CF challenge detection)
///   - TRAWL (FlareSolverr-compatible REST endpoint for CF-protected sites and
///     browser-backed POST/binary fetches via session cache)
use reqwest::Client;

use crate::util::browser_headers;

pub struct FetchResult {
    pub html: String,
    pub final_url: String,
    /// Cookies returned by the server (or by TRAWL after solving a CF challenge).
    /// Each entry is `(name, value)`.
    pub cookies: Vec<(String, String)>,
    /// User-Agent the fetch was made with (the exact browser fingerprint that
    /// earned the cookies above — CF revalidates this on later requests, so
    /// callers reusing `cookies` elsewhere must also replay this UA).
    pub user_agent: String,
}

struct TrawlV1Solution {
    body: String,
    final_url: String,
    cookies: Vec<(String, String)>,
    user_agent: String,
}

static CF_MARKERS: &[&str] = &[
    "cf-chl-",
    "just a moment",
    "cf-turnstile",
    "checking your browser",
    "enable javascript",
    "ddos-guard",
];

fn looks_like_cf_challenge(html: &str) -> bool {
    let lower = html.to_lowercase();
    CF_MARKERS.iter().any(|m| lower.contains(m))
}

/// TRAWL wraps some JSON/text payloads in a Firefox plaintext viewer `<pre>` block.
fn extract_trawl_body(raw: &str) -> String {
    if let Some(start) = raw.find("<pre>") {
        let rest = &raw[start + 5..];
        if let Some(end) = rest.find("</pre>") {
            return rest[..end].to_string();
        }
    }
    raw.to_string()
}

fn parse_trawl_v1_solution(
    resp: &serde_json::Value,
    fallback_url: &str,
) -> Option<TrawlV1Solution> {
    if resp.get("status").and_then(|s| s.as_str()) != Some("ok") {
        return None;
    }

    let solution = resp.get("solution")?;
    let raw_body = solution
        .get("response")
        .and_then(|r| r.as_str())
        .filter(|s| !s.is_empty())?;

    let cookies: Vec<(String, String)> = solution
        .get("cookies")
        .and_then(|c| c.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|c| {
                    let name = c.get("name")?.as_str()?.to_string();
                    let value = c.get("value")?.as_str()?.to_string();
                    Some((name, value))
                })
                .collect()
        })
        .unwrap_or_default();

    Some(TrawlV1Solution {
        body: extract_trawl_body(raw_body),
        final_url: solution
            .get("url")
            .and_then(|u| u.as_str())
            .unwrap_or(fallback_url)
            .to_string(),
        cookies,
        user_agent: solution
            .get("userAgent")
            .and_then(|u| u.as_str())
            .unwrap_or(browser_headers::CHROME_USER_AGENT)
            .to_string(),
    })
}

async fn trawl_v1_request(
    client: &Client,
    trawl_url: &str,
    payload: serde_json::Value,
    timeout_secs: u64,
) -> Option<TrawlV1Solution> {
    let fallback_url = payload
        .get("url")
        .and_then(|u| u.as_str())
        .unwrap_or_default()
        .to_string();

    let resp: serde_json::Value = client
        .post(format!("{trawl_url}/v1"))
        .json(&payload)
        .timeout(std::time::Duration::from_secs(timeout_secs))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    parse_trawl_v1_solution(&resp, &fallback_url)
}

pub async fn fetch_plain(client: &Client, url: &str) -> Option<FetchResult> {
    let resp = client
        .get(url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .ok()?;

    let final_url = resp.url().to_string();
    if !resp.status().is_success() {
        tracing::debug!("fetch_plain HTTP {} for {url}", resp.status());
        return None;
    }
    let html = resp.text().await.ok()?;
    if looks_like_cf_challenge(&html) {
        tracing::debug!("fetch_plain: CF challenge detected for {url}");
        return None;
    }
    Some(FetchResult {
        html,
        final_url,
        cookies: vec![],
        user_agent: browser_headers::CHROME_USER_AGENT.to_string(),
    })
}

pub async fn fetch_trawl(client: &Client, trawl_url: &str, url: &str) -> Option<FetchResult> {
    let solution = trawl_v1_request(
        client,
        trawl_url,
        serde_json::json!({
            "cmd": "request.get",
            "url": url,
            "maxTimeout": 60_000,
        }),
        65,
    )
    .await?;

    Some(FetchResult {
        html: solution.body,
        final_url: solution.final_url,
        cookies: solution.cookies,
        user_agent: solution.user_agent,
    })
}

/// Authenticated POST through TRAWL's browser session cache (FlareSolverr `request.post`).
///
/// Repeat requests to the same domain reuse the warmed browser session (~500ms).
pub async fn fetch_trawl_post(
    client: &Client,
    trawl_url: &str,
    url: &str,
    post_data: &str,
    headers: &[(&str, &str)],
) -> Option<String> {
    let header_map: serde_json::Map<String, serde_json::Value> = headers
        .iter()
        .map(|(k, v)| (k.to_string(), serde_json::Value::String(v.to_string())))
        .collect();

    let solution = trawl_v1_request(
        client,
        trawl_url,
        serde_json::json!({
            "cmd": "request.post",
            "url": url,
            "postData": post_data,
            "headers": header_map,
            "maxTimeout": 60_000,
        }),
        65,
    )
    .await?;

    Some(solution.body)
}

/// Binary GET through TRAWL (e.g. `.torrent` files behind JS bot challenges).
pub async fn fetch_trawl_bytes(client: &Client, trawl_url: &str, url: &str) -> Option<Vec<u8>> {
    let solution = trawl_v1_request(
        client,
        trawl_url,
        serde_json::json!({
            "cmd": "request.get",
            "url": url,
            "maxTimeout": 120_000,
        }),
        125,
    )
    .await?;

    let bytes: Vec<u8> = solution.body.bytes().collect();
    if bytes.is_empty() {
        return None;
    }
    Some(bytes)
}

/// Fetch a page with CF bypass logic.
///
/// - `solve_cloudflare=true` + `trawl_url` present → try TRAWL first, plain as fallback if `http_fallback`
/// - `solve_cloudflare=true` + no TRAWL + `http_fallback` → plain only
/// - `solve_cloudflare=true` + no TRAWL + no `http_fallback` → None (skip)
/// - `solve_cloudflare=false` → plain only
pub async fn fetch_for_indexer(
    client: &Client,
    trawl_url: Option<&str>,
    url: &str,
    solve_cloudflare: bool,
    http_fallback: bool,
) -> Option<FetchResult> {
    if solve_cloudflare {
        if let Some(trawl) = trawl_url {
            if let Some(r) = fetch_trawl(client, trawl, url).await {
                return Some(r);
            }
            if http_fallback {
                return fetch_plain(client, url).await;
            }
            return None;
        }
        if http_fallback {
            return fetch_plain(client, url).await;
        }
        return None; // CF required, no TRAWL configured, no fallback
    }
    fetch_plain(client, url).await
}
