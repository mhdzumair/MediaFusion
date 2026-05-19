/// HTTP fetch abstraction for public indexer scrapers.
///
/// Two modes:
///   - Plain HTTP (browser UA, CF challenge detection)
///   - Byparr (FlareSolverr-compatible REST endpoint for CF-protected sites)
use reqwest::Client;

pub struct FetchResult {
    pub html: String,
    pub final_url: String,
    /// Cookies returned by the server (or by Byparr after solving a CF challenge).
    /// Each entry is `(name, value)`.
    pub cookies: Vec<(String, String)>,
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

pub async fn fetch_plain(client: &Client, url: &str) -> Option<FetchResult> {
    let resp = client
        .get(url)
        .header(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
             (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        .header(
            "Accept",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        .header("Accept-Language", "en-US,en;q=0.5")
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
    })
}

pub async fn fetch_byparr(client: &Client, byparr_url: &str, url: &str) -> Option<FetchResult> {
    #[derive(serde::Serialize)]
    struct ByparrReq<'a> {
        cmd: &'a str,
        url: &'a str,
        #[serde(rename = "maxTimeout")]
        max_timeout: u64,
    }

    let body = ByparrReq {
        cmd: "request.get",
        url,
        max_timeout: 60_000,
    };

    let resp: serde_json::Value = client
        .post(format!("{byparr_url}/v1"))
        .json(&body)
        .timeout(std::time::Duration::from_secs(65))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    let solution = resp.get("solution")?;

    let html = solution
        .get("response")
        .and_then(|r| r.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())?;

    let final_url = solution
        .get("url")
        .and_then(|u| u.as_str())
        .unwrap_or(url)
        .to_string();

    // Extract cookies so callers can reuse the CF clearance token for direct requests.
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

    Some(FetchResult {
        html,
        final_url,
        cookies,
    })
}

/// Fetch a page with CF bypass logic.
///
/// - `solve_cloudflare=true` + `byparr_url` present → try Byparr first, plain as fallback if `http_fallback`
/// - `solve_cloudflare=true` + no Byparr + `http_fallback` → plain only
/// - `solve_cloudflare=true` + no Byparr + no `http_fallback` → None (skip)
/// - `solve_cloudflare=false` → plain only
pub async fn fetch_for_indexer(
    client: &Client,
    byparr_url: Option<&str>,
    url: &str,
    solve_cloudflare: bool,
    http_fallback: bool,
) -> Option<FetchResult> {
    if solve_cloudflare {
        if let Some(byparr) = byparr_url {
            if let Some(r) = fetch_byparr(client, byparr, url).await {
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
        return None; // CF required, no Byparr configured, no fallback
    }
    fetch_plain(client, url).await
}
