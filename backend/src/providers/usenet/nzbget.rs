/// NZBGet usenet playback provider.
///
/// Flow:
///   1. POST /jsonrpc method=append  — submit NZB for download
///   2. Poll method=listgroups + method=history until status contains SUCCESS
///   3. WebDAV PROPFIND on the completed download directory
///   4. Select best video file and build a credentialed WebDAV URL
use std::time::Duration;

use serde_json::Value;

use crate::providers::ProviderError;

use super::{
    config_fields::{
        PASSWORD_KEYS, URL_KEYS, USERNAME_KEYS, WEBDAV_PASS_KEYS, WEBDAV_URL_KEYS,
        WEBDAV_USER_KEYS, str_field,
    },
    webdav,
};

/// `config` is the raw JSON from `StreamingProvider.nzbget_config`.
/// `submission_url`: MediaFusion proxy URL (empty on localhost).
/// `fallback_url`:   Resolved indexer URL with API key (for file-upload fallback).
pub async fn get_url(
    http: &reqwest::Client,
    config: &Value,
    submission_url: &str,
    fallback_url: &str,
    name: &str,
    season: i32,
    episode: i32,
) -> Result<String, ProviderError> {
    let base_url = str_field(config, URL_KEYS)
        .ok_or_else(|| ProviderError::api("NZBGet: no url in config", "invalid_config.mp4"))?
        .trim_end_matches('/')
        .to_string();
    let username = str_field(config, USERNAME_KEYS)
        .unwrap_or("nzbget")
        .to_string();
    let password = str_field(config, PASSWORD_KEYS)
        .unwrap_or("tegbzn6789")
        .to_string();
    let webdav_url = str_field(config, WEBDAV_URL_KEYS)
        .unwrap_or_default()
        .to_string();
    let webdav_user = str_field(config, WEBDAV_USER_KEYS)
        .unwrap_or(&username)
        .to_string();
    let webdav_pass = str_field(config, WEBDAV_PASS_KEYS)
        .unwrap_or(&password)
        .to_string();

    let rpc_url = format!("{base_url}/jsonrpc");

    // Step 1: submit NZB — proxy URL first, base64-content fallback
    let nzb_id = submit_nzb(
        http,
        &rpc_url,
        &username,
        &password,
        submission_url,
        fallback_url,
        name,
    )
    .await?;

    // Step 2: poll
    let dir_name = poll(http, &rpc_url, &username, &password, nzb_id).await?;

    // Steps 3 & 4: WebDAV
    if webdav_url.is_empty() {
        return Err(ProviderError::api(
            "NZBGet: no webdav_url configured — cannot serve file",
            "invalid_config.mp4",
        ));
    }
    let hrefs = webdav::list(http, &webdav_url, &dir_name, &webdav_user, &webdav_pass).await?;
    let path = webdav::select_video(&hrefs, name, season, episode).ok_or_else(|| {
        ProviderError::api(
            "NZBGet: no matching video in WebDAV",
            "no_video_file_found.mp4",
        )
    })?;

    Ok(webdav::url_with_creds(
        &webdav_url,
        &path,
        &webdav_user,
        &webdav_pass,
    ))
}

// ─── NZB submission (proxy URL → base64-content fallback) ─────────────────────

async fn submit_nzb(
    http: &reqwest::Client,
    rpc_url: &str,
    username: &str,
    password: &str,
    submission_url: &str,
    fallback_url: &str,
    name: &str,
) -> Result<i64, ProviderError> {
    // Attempt 1: submit via proxy URL (when available)
    if !submission_url.is_empty() {
        let resp: Value = http
            .post(rpc_url)
            .basic_auth(username, Some(password))
            .json(&serde_json::json!({
                "method": "append",
                "params": [
                    format!("{name}.nzb"),
                    submission_url,
                    "MediaFusion",
                    0, false, false, "", 0, "SCORE",
                    [{"*Unpack:": "yes"}]
                ]
            }))
            .send()
            .await?
            .json()
            .await?;

        if let Some(id) = resp
            .get("result")
            .and_then(|v| v.as_i64())
            .filter(|&id| id > 0)
        {
            return Ok(id);
        }
        tracing::debug!("NZBGet: append by URL failed, falling back to content upload");
    }

    // Attempt 2: download NZB ourselves from fallback_url, submit as base64
    if fallback_url.is_empty() {
        return Err(ProviderError::api(
            "NZBGet: no NZB URL available for content upload",
            "usenet_transfer_error.mp4",
        ));
    }
    let nzb_bytes = super::fetch_nzb_bytes(http, fallback_url).await?;
    use base64::Engine;
    let b64 = base64::engine::general_purpose::STANDARD.encode(&nzb_bytes);

    let resp2: Value = http
        .post(rpc_url)
        .basic_auth(username, Some(password))
        .json(&serde_json::json!({
            "method": "appendcontent",
            "params": [
                format!("{name}.nzb"),
                b64,
                "MediaFusion",
                0, false, false, "", 0, "SCORE",
                [{"*Unpack:": "yes"}]
            ]
        }))
        .send()
        .await?
        .json()
        .await?;

    resp2
        .get("result")
        .and_then(|v| v.as_i64())
        .filter(|&id| id > 0)
        .ok_or_else(|| {
            let err = resp2
                .get("error")
                .and_then(|v| v.get("message"))
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            ProviderError::api(
                format!("NZBGet: content upload failed: {err}"),
                "usenet_transfer_error.mp4",
            )
        })
}

// ─── Polling ───────────────────────────────────────────────────────────────────

async fn poll(
    http: &reqwest::Client,
    rpc_url: &str,
    username: &str,
    password: &str,
    nzb_id: i64,
) -> Result<String, ProviderError> {
    for _ in 0u32..60 {
        tokio::time::sleep(Duration::from_secs(5)).await;

        // Still in active queue?
        let groups: Value = http
            .post(rpc_url)
            .basic_auth(username, Some(password))
            .json(&serde_json::json!({"method": "listgroups", "params": [0]}))
            .send()
            .await?
            .json()
            .await?;

        let still_active = groups
            .get("result")
            .and_then(|v| v.as_array())
            .map(|g| {
                g.iter()
                    .any(|i| i.get("NZBID").and_then(|v| v.as_i64()) == Some(nzb_id))
            })
            .unwrap_or(false);

        if still_active {
            continue;
        }

        // Check history
        let hist: Value = http
            .post(rpc_url)
            .basic_auth(username, Some(password))
            .json(&serde_json::json!({"method": "history", "params": [false]}))
            .send()
            .await?
            .json()
            .await?;

        let items = hist.get("result").and_then(|v| v.as_array());
        if let Some(items) = items {
            for item in items {
                if item.get("ID").and_then(|v| v.as_i64()) != Some(nzb_id) {
                    continue;
                }
                let status = item.get("Status").and_then(|v| v.as_str()).unwrap_or("");
                if status.contains("SUCCESS") || status == "DOWNLOADED" {
                    let dest = item
                        .get("DestDir")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default();
                    let dir = std::path::Path::new(dest)
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or_default()
                        .to_string();
                    return Ok(dir);
                } else if status.contains("FAILURE") || status.contains("DELETED") {
                    return Err(ProviderError::api(
                        format!("NZBGet: download {status}"),
                        "usenet_transfer_error.mp4",
                    ));
                }
            }
        }
    }
    Err(ProviderError::api(
        "NZBGet: download timed out after 5 minutes",
        "usenet_transfer_error.mp4",
    ))
}

// ─── Config field helper ───────────────────────────────────────────────────────
// Re-exported via config_fields for tests and sibling modules.
