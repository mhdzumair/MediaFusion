//! Browserless v2 integration for JavaScript-challenge-protected downloads.
//!
//! Some sites (e.g. sport-video.org.ua) protect their download endpoints with
//! custom bot challenges (adm.tools) that require a real browser to solve.
//!
//! This module uses the browserless v2 `/chromium/function` REST endpoint to run
//! a Playwright function server-side.  The function navigates to the site origin,
//! then executes our challenge-solving `fetch()` inside the page context (same
//! cookie jar, same browser fingerprint) and returns the binary payload as base64.
//!
//! The adm.tools challenge flow:
//!   1. GET the target URL → 429 HTML containing `form.append('___ack', eval('...'))`
//!   2. `eval(...)` the embedded JS expression to produce the ACK token
//!   3. POST the token back as FormData with field `___ack`
//!   4. Retry GET → 200 + actual binary content

use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64};
use reqwest::Client;
use tracing::{debug, warn};

/// Playwright function sent to browserless v2's `/chromium/function` endpoint.
///
/// The outer function runs in Node.js (Playwright context).
/// `page.evaluate(...)` runs the inner async arrow function inside Chrome's JS engine,
/// giving it access to the page's cookie jar and browser fingerprint.
const FETCH_TORRENT_FUNCTION: &str = r#"
export default async ({ page, context }) => {
    const { refererUrl, torrentUrl } = context;

    // Navigate to the category page (a static HTML page that doesn't trigger the
    // challenge) to establish the browser session on the correct origin.
    // We must NOT navigate to the site root — it has the adm.tools challenge which
    // fires location.reload() and destroys the execution context.
    // waitUntil:'load' waits for the load event (networkidle not supported in this version).
    await page.goto(refererUrl, { waitUntil: 'load', timeout: 30000 });

    // Run the challenge-solving fetch entirely inside the browser page context.
    // Using credentials:'include' ensures the browser's cookie jar is used,
    // so the ___ack POST sets a session cookie that the follow-up GET reuses.
    const result = await page.evaluate(async (targetUrl) => {
        const toBase64 = (bytes) => {
            let binary = '';
            const chunkSize = 0x8000;
            for (let i = 0; i < bytes.length; i += chunkSize) {
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
            }
            return btoa(binary);
        };

        for (let attempt = 0; attempt < 4; attempt++) {
            const resp = await fetch(targetUrl, { credentials: 'include' });
            const buf  = await resp.arrayBuffer();
            const bytes = new Uint8Array(buf);

            // A valid bencoded torrent starts with 'd' (ASCII 100).
            if (resp.status === 200 && bytes.length > 0 && bytes[0] === 100) {
                return { ok: true, base64: toBase64(bytes) };
            }

            // Parse the adm.tools challenge token embedded in the 429 HTML.
            const text  = new TextDecoder().decode(bytes);
            const match = text.match(/form\.append\('___ack',\s*eval\('([^']+)'\)\)/);
            if (!match) return { ok: false, reason: 'no_challenge', status: resp.status };

            // Evaluate the challenge expression and POST the solution.
            let ack;
            try   { ack = Function('"use strict"; return (' + match[1] + ');')(); }
            catch (e) { return { ok: false, reason: 'eval_error' }; }

            const form = new FormData();
            form.append('___ack', String(ack));
            await fetch(targetUrl, { method: 'POST', body: form, credentials: 'include' });
            await new Promise(r => setTimeout(r, 600));
        }

        return { ok: false, reason: 'max_retries' };
    }, torrentUrl);

    return result;
};
"#;

/// Playwright function that replays a Cloudflare-cleared cookie jar (harvested
/// elsewhere, e.g. via byparr) through browserless to make one authenticated
/// same-origin AJAX POST.
///
/// Cloudflare revalidates the clearance cookie against the User-Agent that
/// earned it, not the TLS fingerprint of the client presenting it — a bare
/// HTTP client (curl/reqwest) fails this even with a matching UA because it
/// isn't running real browser JS, but browserless's actual Chrome passes as
/// long as its UA is spoofed to match. We navigate to `refererUrl` first so
/// the in-page `fetch()` is same-origin (no CORS) and cookies apply cleanly.
const POST_WITH_COOKIES_FUNCTION: &str = r#"
export default async ({ page, context }) => {
    const { refererUrl, ajaxUrl, postData, cookies, userAgent } = context;

    await page.setUserAgent(userAgent);
    await page.setCookie(...cookies);
    await page.goto(refererUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });

    const result = await page.evaluate(async (url, body) => {
        const resp = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
            },
            body,
            credentials: 'include',
        });
        return { status: resp.status, text: await resp.text() };
    }, ajaxUrl, postData);

    return result;
};
"#;

/// Make an authenticated POST to `ajax_url` by replaying a cookie jar (and
/// the User-Agent that earned it) through a real browserless Chrome instance.
///
/// # Parameters
/// - `referer_url` – same-origin page to navigate to first, so the in-page
///   `fetch()` isn't blocked by CORS
/// - `cookies`      – `(name, value)` pairs to inject before navigating
/// - `user_agent`   – must match the UA used when the cookies were obtained
///
/// Returns the response body text on success (regardless of HTTP status —
/// callers inspect the body themselves), or `None` on transport failure.
pub async fn post_with_cookies_via_browser(
    client: &Client,
    browserless_url: &str,
    referer_url: &str,
    ajax_url: &str,
    post_data: &str,
    cookies: &[(String, String)],
    user_agent: &str,
) -> Option<String> {
    #[derive(serde::Serialize)]
    struct BrowserCookie<'a> {
        name: &'a str,
        value: &'a str,
        domain: &'a str,
        path: &'a str,
    }

    let domain = referer_url
        .split("://")
        .nth(1)
        .and_then(|s| s.split('/').next())
        .unwrap_or_default();

    let cookies: Vec<BrowserCookie> = cookies
        .iter()
        .map(|(name, value)| BrowserCookie {
            name,
            value,
            domain,
            path: "/",
        })
        .collect();

    let endpoint = format!(
        "{}/chromium/function",
        browserless_url.trim_end_matches('/')
    );

    let body = serde_json::json!({
        "code": POST_WITH_COOKIES_FUNCTION,
        "context": {
            "refererUrl": referer_url,
            "ajaxUrl": ajax_url,
            "postData": post_data,
            "cookies": cookies,
            "userAgent": user_agent,
        },
    });

    debug!("browser: POST {endpoint} for {ajax_url}");

    let resp = match client
        .post(&endpoint)
        .json(&body)
        .timeout(std::time::Duration::from_secs(60))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            warn!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "browser: request to browserless failed: {e}"
            );
            return None;
        }
    };

    if !resp.status().is_success() {
        warn!(
            "browser: browserless returned HTTP {} for {ajax_url}",
            resp.status()
        );
        return None;
    }

    let result: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| warn!("browser: failed to parse browserless response: {e}"))
        .ok()?;

    if let Some(err) = result.get("error") {
        warn!("browser: page function errored for {ajax_url}: {err}");
        return None;
    }

    result
        .get("text")
        .and_then(|t| t.as_str())
        .map(String::from)
}

/// Download a binary file (e.g. `.torrent`) through a browserless v2 Chrome instance,
/// solving any JavaScript bot challenge in the process.
///
/// # Parameters
/// - `client`          – shared reqwest HTTP client
/// - `browserless_url` – base URL of the browserless v2 container (e.g. `http://browserless:3000`)
/// - `referer_url`     – site origin to navigate to first (primes the browser session)
/// - `torrent_url`     – the protected binary download URL
///
/// Returns `Some(bytes)` on success (non-empty, starts with `b"d"` for a valid torrent),
/// or `None` on any failure.
pub async fn fetch_torrent_via_browser(
    client: &Client,
    browserless_url: &str,
    referer_url: &str,
    torrent_url: &str,
) -> Option<Vec<u8>> {
    let endpoint = format!(
        "{}/chromium/function",
        browserless_url.trim_end_matches('/')
    );

    let body = serde_json::json!({
        "code": FETCH_TORRENT_FUNCTION,
        "context": {
            "refererUrl": referer_url,
            "torrentUrl": torrent_url,
        },
    });

    debug!("browser: POST {endpoint} for {torrent_url}");

    let resp = match client
        .post(&endpoint)
        .json(&body)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            warn!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "browser: request to browserless failed: {e}"
            );
            return None;
        }
    };

    if !resp.status().is_success() {
        warn!(
            "browser: browserless returned HTTP {} for {torrent_url}",
            resp.status()
        );
        return None;
    }

    let result: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| warn!("browser: failed to parse browserless response: {e}"))
        .ok()?;

    if !result["ok"].as_bool().unwrap_or(false) {
        warn!(
            "browser: torrent fetch failed — reason={:?} url={torrent_url}",
            result["reason"].as_str().unwrap_or("unknown")
        );
        return None;
    }

    let b64 = result["base64"].as_str()?;
    let bytes = BASE64
        .decode(b64)
        .map_err(|e| warn!("browser: base64 decode failed: {e}"))
        .ok()?;

    if bytes.is_empty() {
        warn!("browser: decoded empty payload for {torrent_url}");
        return None;
    }

    debug!("browser: fetched {} bytes for {torrent_url}", bytes.len());
    Some(bytes)
}

/// Playwright function: navigate to a URL and return the response body as text.
const FETCH_TEXT_FUNCTION: &str = r#"
export default async ({ page, context }) => {
    const { feedUrl, userAgent } = context;
    if (userAgent) {
        await page.setUserAgent(userAgent);
    }
    const resp = await page.goto(feedUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
    if (!resp) {
        return { ok: false, reason: 'no_response' };
    }
    const status = resp.status();
    const text = await resp.text();
    return { ok: status >= 200 && status < 300, status, text };
};
"#;

/// Fetch a text/XML document through browserless (real Chrome — bypasses Reddit 429/403).
pub async fn fetch_text_via_browser(
    client: &Client,
    browserless_url: &str,
    url: &str,
    user_agent: Option<&str>,
) -> Option<(u16, String)> {
    let endpoint = format!(
        "{}/chromium/function",
        browserless_url.trim_end_matches('/')
    );

    let body = serde_json::json!({
        "code": FETCH_TEXT_FUNCTION,
        "context": {
            "feedUrl": url,
            "userAgent": user_agent,
        },
    });

    debug!("browser: POST {endpoint} for text fetch {url}");

    let resp = match client
        .post(&endpoint)
        .json(&body)
        .timeout(std::time::Duration::from_secs(90))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            warn!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "browser: text fetch request failed: {e}"
            );
            return None;
        }
    };

    if !resp.status().is_success() {
        warn!(
            "browser: browserless returned HTTP {} for text fetch {url}",
            resp.status()
        );
        return None;
    }

    let result: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| warn!("browser: failed to parse text fetch response: {e}"))
        .ok()?;

    if let Some(err) = result.get("error") {
        warn!("browser: page function errored for text fetch {url}: {err}");
        return None;
    }

    let status = result["status"].as_u64().unwrap_or(0) as u16;
    let text = result["text"].as_str().unwrap_or("").to_string();

    if !result["ok"].as_bool().unwrap_or(false) {
        debug!(
            "browser: text fetch HTTP {status} for {url} ({} bytes)",
            text.len()
        );
        return Some((status, text));
    }

    if text.trim().is_empty() {
        warn!("browser: empty text payload for {url}");
        return None;
    }

    debug!(
        "browser: fetched {} bytes (HTTP {status}) for {url}",
        text.len()
    );
    Some((status, text))
}
