/// SABnzbd / NzbDAV usenet playback provider.
///
/// Both services expose a SABnzbd-compatible REST API, so one implementation
/// covers both. NzbDAV adds a WebDAV server on top for file serving.
///
/// Flow:
///   1. GET  /api?mode=addurl  — submit NZB for download
///   2. Poll /api?mode=queue + mode=history until status = Completed
///   3. WebDAV PROPFIND on the completed download directory
///   4. Select best video file and build a credentialed WebDAV URL
use std::time::Duration;

use serde_json::Value;

use crate::providers::ProviderError;

use super::{
    config_fields::{
        API_KEY_KEYS, URL_KEYS, WEBDAV_PASS_KEYS, WEBDAV_URL_KEYS, WEBDAV_USER_KEYS, str_field,
    },
    webdav,
};

/// `config` is the raw JSON from `StreamingProvider.sabnzbd_config` / `nzbdav_config`.
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
        .ok_or_else(|| ProviderError::api("SABnzbd: no url in config", "invalid_config.mp4"))?
        .trim_end_matches('/')
        .to_string();
    let api_key = str_field(config, API_KEY_KEYS)
        .ok_or_else(|| ProviderError::api("SABnzbd: no api_key in config", "invalid_config.mp4"))?
        .to_string();
    let webdav_url = str_field(config, WEBDAV_URL_KEYS)
        .unwrap_or_default()
        .to_string();
    let webdav_user = str_field(config, WEBDAV_USER_KEYS)
        .unwrap_or_default()
        .to_string();
    let webdav_pass = str_field(config, WEBDAV_PASS_KEYS)
        .unwrap_or_default()
        .to_string();

    let api_url = format!("{base_url}/api");

    // Step 1: submit NZB — proxy URL first, file-upload via fallback_url
    let nzo_id = submit_nzb(http, &api_url, &api_key, submission_url, fallback_url, name).await?;

    // Step 2: poll (max ~5 min)
    let dir_name = poll(http, &api_url, &api_key, &nzo_id).await?;

    // Step 3 & 4: WebDAV listing → credentialed URL
    if webdav_url.is_empty() {
        return Err(ProviderError::api(
            "SABnzbd: no webdav_url configured — cannot serve file",
            "invalid_config.mp4",
        ));
    }
    let hrefs = webdav::list(http, &webdav_url, &dir_name, &webdav_user, &webdav_pass).await?;
    let path = webdav::select_video(&hrefs, name, season, episode).ok_or_else(|| {
        ProviderError::api(
            "SABnzbd: no matching video in WebDAV",
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

// ─── NZB submission (proxy URL → file-upload fallback) ────────────────────────

pub async fn submit_nzb(
    http: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    submission_url: &str,
    fallback_url: &str,
    name: &str,
) -> Result<String, ProviderError> {
    // Attempt 1: submit via proxy URL (when available)
    if !submission_url.is_empty() {
        let add: Value = http
            .get(api_url)
            .query(&[
                ("mode", "addurl"),
                ("name", submission_url),
                ("nzbname", name),
                ("cat", "MediaFusion"),
                ("apikey", api_key),
                ("output", "json"),
            ])
            .send()
            .await?
            .json()
            .await?;

        if let Some(nzo_id) = extract_nzo_id(&add) {
            return Ok(nzo_id);
        }
        tracing::debug!("SABnzbd: addurl failed ({add}), falling back to file upload");
    }

    // Attempt 2: download NZB bytes ourselves, upload as file
    if fallback_url.is_empty() {
        return Err(ProviderError::api(
            "SABnzbd: no NZB URL available for file upload",
            "usenet_transfer_error.mp4",
        ));
    }
    let nzb_bytes = super::fetch_nzb_bytes(http, fallback_url).await?;
    let part = reqwest::multipart::Part::bytes(nzb_bytes)
        .file_name(format!("{name}.nzb"))
        .mime_str("application/x-nzb")
        .map_err(|e| ProviderError::Other(format!("SABnzbd: mime error: {e}")))?;
    let form = reqwest::multipart::Form::new()
        .part("name", reqwest::multipart::Part::text(name.to_string()))
        .part("cat", reqwest::multipart::Part::text("MediaFusion"))
        .part(
            "apikey",
            reqwest::multipart::Part::text(api_key.to_string()),
        )
        .part("output", reqwest::multipart::Part::text("json"))
        .part("mode", reqwest::multipart::Part::text("addfile"))
        .part("nzbfile", part);

    let add2: Value = http
        .post(api_url)
        .multipart(form)
        .send()
        .await?
        .json()
        .await?;

    extract_nzo_id(&add2).ok_or_else(|| {
        ProviderError::api(
            format!("SABnzbd: file upload failed: {add2}"),
            "usenet_transfer_error.mp4",
        )
    })
}

fn extract_nzo_id(resp: &Value) -> Option<String> {
    resp.get("nzo_ids")
        .and_then(|v| v.as_array())
        .and_then(|a| a.first())
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

// ─── Polling ───────────────────────────────────────────────────────────────────

async fn poll(
    http: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    nzo_id: &str,
) -> Result<String, ProviderError> {
    for _ in 0u32..60 {
        tokio::time::sleep(Duration::from_secs(5)).await;

        if !in_queue(http, api_url, api_key, nzo_id).await?
            && let Some(result) = in_history(http, api_url, api_key, nzo_id).await?
        {
            return result;
        }
    }
    Err(ProviderError::api(
        "SABnzbd: download timed out after 5 minutes",
        "usenet_transfer_error.mp4",
    ))
}

async fn in_queue(
    http: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    nzo_id: &str,
) -> Result<bool, ProviderError> {
    let q: Value = http
        .get(api_url)
        .query(&[("mode", "queue"), ("apikey", api_key), ("output", "json")])
        .send()
        .await?
        .json()
        .await?;

    Ok(q.get("queue")
        .and_then(|q| q.get("slots"))
        .and_then(|v| v.as_array())
        .map(|slots| {
            slots
                .iter()
                .any(|s| s.get("nzo_id").and_then(|v| v.as_str()) == Some(nzo_id))
        })
        .unwrap_or(false))
}

/// Returns `Ok(Some(Ok(dir_name)))` when completed, `Ok(Some(Err(...)))` on failure,
/// `Ok(None)` when not yet in history.
async fn in_history(
    http: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    nzo_id: &str,
) -> Result<Option<Result<String, ProviderError>>, ProviderError> {
    let h: Value = http
        .get(api_url)
        .query(&[
            ("mode", "history"),
            ("limit", "200"),
            ("apikey", api_key),
            ("output", "json"),
        ])
        .send()
        .await?
        .json()
        .await?;

    let slots = match h
        .get("history")
        .and_then(|h| h.get("slots"))
        .and_then(|v| v.as_array())
    {
        Some(s) => s,
        None => return Ok(None),
    };

    for slot in slots {
        if slot.get("nzo_id").and_then(|v| v.as_str()) != Some(nzo_id) {
            continue;
        }
        let status = slot.get("status").and_then(|v| v.as_str()).unwrap_or("");
        if status == "Completed" || status == "Moved" {
            let storage = slot.get("storage").and_then(|v| v.as_str()).unwrap_or("");
            let dir = std::path::Path::new(storage)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(nzo_id)
                .to_string();
            return Ok(Some(Ok(dir)));
        } else if status == "Failed" {
            let msg = slot
                .get("fail_message")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            return Ok(Some(Err(ProviderError::api(
                format!("SABnzbd: download failed: {msg}"),
                "usenet_transfer_error.mp4",
            ))));
        }
    }
    Ok(None)
}

// ─── Config field helper ───────────────────────────────────────────────────────
// Re-exported via config_fields for tests and sibling modules.
