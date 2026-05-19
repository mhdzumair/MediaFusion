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

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
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

    let resp = client
        .post(&endpoint)
        .json(&body)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
        .map_err(|e| warn!("browser: request to browserless failed: {e}"))
        .ok()?;

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
