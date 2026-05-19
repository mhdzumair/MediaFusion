/// TorBox usenet playback provider.
///
/// Flow:
///   1. GET  /usenet/mylist                  — check if already downloaded
///   2. POST /usenet/createusenetdownload    — submit NZB (URL first; file-upload fallback)
///   3. GET  /usenet/requestdl              — obtain time-limited CDN URL
///
/// Submission strategy:
///   a) Submit the NZB URL directly — works when TorBox can fetch it (public or with embedded key).
///   b) If the URL submission fails, download the NZB ourselves (using the resolved URL
///      that carries the user's API key) and upload the raw bytes as a multipart file.
///      This handles Newznab indexers that require auth TorBox can't supply.
use serde_json::Value;
use tracing::debug;

use crate::providers::{
    torrents::transport::{append_query, encode_form_body, MediaFlowForward},
    ProviderError,
};

use super::{is_video_name, loose_name_match};

const BASE: &str = "https://api.torbox.app/v1/api";

/// `submission_url`: MediaFusion proxy URL (preferred — keeps raw indexer creds private).
///                   Empty when running on localhost.
/// `fallback_url`:   Resolved indexer URL with user's API key (used for file-upload
///                   when submission_url is empty or URL-submission fails).
#[allow(clippy::too_many_arguments)]
pub async fn get_url(
    http: &reqwest::Client,
    token: &str,
    submission_url: &str,
    fallback_url: &str,
    nzb_guid: &str,
    name: &str,
    season: i32,
    episode: i32,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    if token.is_empty() {
        return Err(ProviderError::api(
            "TorBox: no API token configured",
            "invalid_token.mp4",
        ));
    }

    // Step 1: check if already in user's download list
    let list = mylist(http, token, forward).await?;

    let usenet_info = match find_item(&list, name, nzb_guid) {
        Some(item) if is_ready(item) => item.clone(),
        Some(_) => {
            // Queued/downloading — tell the client to retry
            return Err(ProviderError::api(
                "TorBox: download in progress — retry shortly",
                "torrent_downloading.mp4",
            ));
        }
        None => {
            if submission_url.is_empty() && fallback_url.is_empty() {
                return Err(ProviderError::api(
                    "TorBox: no NZB URL available",
                    "stream_not_found.mp4",
                ));
            }
            // Step 2: submit NZB — proxy URL first (or file-upload when localhost)
            submit_nzb(
                http,
                token,
                submission_url,
                fallback_url,
                name,
                nzb_guid,
                forward,
            )
            .await?
        }
    };

    // Step 3: request CDN download link
    let usenet_id = usenet_info
        .get("id")
        .and_then(|v| v.as_i64())
        .ok_or_else(|| ProviderError::api("TorBox: item has no id", "usenet_transfer_error.mp4"))?;
    let file_id = select_file(&usenet_info, name, season, episode);

    let requestdl_url = format!("{BASE}/usenet/requestdl");
    let resp: Value = if let Some(fwd) = forward {
        // token= is a query param, not Bearer — embed all params in dest URL
        let dest = append_query(
            &requestdl_url,
            &[
                ("token", token),
                ("usenet_id", &usenet_id.to_string()),
                ("file_id", &file_id.to_string()),
                ("zip_link", "false"),
            ],
        );
        fwd.get_no_auth(http, &dest).await?.json().await?
    } else {
        http.get(&requestdl_url)
            .query(&[
                ("token", token.to_string()),
                ("usenet_id", usenet_id.to_string()),
                ("file_id", file_id.to_string()),
                ("zip_link", "false".to_string()),
            ])
            .send()
            .await?
            .json()
            .await?
    };

    resp.get("data")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            ProviderError::api(
                format!("TorBox: no data in requestdl response: {resp}"),
                "usenet_transfer_error.mp4",
            )
        })
}

// ─── NZB submission (URL → file-upload fallback) ──────────────────────────────

/// Submit an NZB to TorBox and return the resulting usenet item once it is
/// queued or cached.
///
/// Strategy:
///   1. If `submission_url` is non-empty, try it first (MediaFusion proxy URL).
///   2. If URL submission fails or `submission_url` is empty, download NZB bytes
///      from `fallback_url` and upload them as a multipart file.
async fn submit_nzb(
    http: &reqwest::Client,
    token: &str,
    submission_url: &str,
    fallback_url: &str,
    name: &str,
    nzb_guid: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    // Attempt 1a: submit proxy URL if available (keeps raw indexer creds private)
    // Attempt 1b: if no proxy (localhost), submit fallback_url only when TorBox can
    //             access it natively (its own search-api domain). For third-party
    //             URLs TorBox would time out trying to authenticate — skip to file upload.
    let url_to_try = if !submission_url.is_empty() {
        submission_url
    } else if !fallback_url.is_empty() && is_torbox_native_url(fallback_url) {
        fallback_url
    } else {
        ""
    };

    if !url_to_try.is_empty() {
        let add = add_by_url(http, token, url_to_try, forward).await?;
        let success = add
            .get("success")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let detail = add
            .get("detail")
            .and_then(|v| v.as_str())
            .unwrap_or_default();

        if success {
            debug!("TorBox: URL submission accepted: {detail}");
            return wait_for_item(http, token, name, nzb_guid, forward).await;
        }
        debug!("TorBox: URL submission failed ({detail}), falling back to file upload");
    }

    // Attempt 2: download NZB bytes ourselves, upload as multipart file.
    // Only usable when fallback_url is a URL we can GET (i.e. it carries auth creds).
    if fallback_url.is_empty() {
        return Err(ProviderError::api(
            "TorBox: no NZB URL available for file upload",
            "usenet_transfer_error.mp4",
        ));
    }
    debug!("TorBox: fetching NZB bytes from indexer");
    let nzb_bytes = super::fetch_nzb_bytes(http, fallback_url).await?;
    debug!(
        "TorBox: fetched {} bytes, uploading to TorBox",
        nzb_bytes.len()
    );

    // Sanity check: a valid NZB must start with XML or the NZB doctype.
    // Newznab error responses are also XML but very short (~80-200 bytes).
    if nzb_bytes.len() < 200 {
        let snippet = String::from_utf8_lossy(&nzb_bytes[..nzb_bytes.len().min(200)]);
        if snippet.contains("<error") {
            return Err(ProviderError::api(
                format!(
                    "indexer returned an error instead of NZB: {}",
                    snippet.trim()
                ),
                "usenet_transfer_error.mp4",
            ));
        }
    }

    let add2 = add_by_file(http, token, name, nzb_bytes, forward).await?;
    let success2 = add2
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let detail2 = add2
        .get("detail")
        .and_then(|v| v.as_str())
        .unwrap_or_default();
    if !success2 {
        tracing::warn!("TorBox: file upload failed — full response: {add2}");
    }

    if !success2 {
        return Err(ProviderError::api(
            format!("TorBox: file upload failed: {detail2}"),
            "usenet_transfer_error.mp4",
        ));
    }

    debug!("TorBox: file upload accepted: {detail2}");
    wait_for_item(http, token, name, nzb_guid, forward).await
}

/// After a successful submission, re-fetch mylist to get the full item.
/// Returns the item if ready, or a "retry" error if still downloading.
async fn wait_for_item(
    http: &reqwest::Client,
    token: &str,
    name: &str,
    nzb_guid: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let list = mylist(http, token, forward).await?;
    match find_item(&list, name, nzb_guid) {
        Some(item) if is_ready(item) => Ok(item.clone()),
        Some(_) => Err(ProviderError::api(
            "TorBox: submitted — download queued, retry shortly",
            "torrent_downloading.mp4",
        )),
        None => Err(ProviderError::api(
            "TorBox: submitted but item not found in list yet, retry shortly",
            "torrent_downloading.mp4",
        )),
    }
}

async fn add_by_url(
    http: &reqwest::Client,
    token: &str,
    nzb_url: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE}/usenet/createusenetdownload");
    if let Some(fwd) = forward {
        let body_str = encode_form_body(&[("link", nzb_url)]);
        fwd.post_form(http, &url, token, body_str)
            .await?
            .json()
            .await
            .map_err(Into::into)
    } else {
        http.post(&url)
            .bearer_auth(token)
            .form(&[("link", nzb_url)])
            .send()
            .await?
            .json()
            .await
            .map_err(Into::into)
    }
}

async fn add_by_file(
    http: &reqwest::Client,
    token: &str,
    name: &str,
    nzb_bytes: Vec<u8>,
    forward: Option<&MediaFlowForward>,
) -> Result<Value, ProviderError> {
    let url = format!("{BASE}/usenet/createusenetdownload");
    let filename = format!("{name}.nzb");

    if let Some(fwd) = forward {
        // Build multipart body manually so we can forward it as raw bytes
        let boundary = "mediafusion_boundary_nzb_upload";
        let mut body: Vec<u8> = Vec::new();
        body.extend_from_slice(
            format!(
                "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/x-nzb\r\n\r\n"
            )
            .as_bytes(),
        );
        body.extend_from_slice(&nzb_bytes);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());

        let content_type = format!("multipart/form-data; boundary={boundary}");
        fwd.post_raw(http, &url, token, &content_type, body)
            .await?
            .json()
            .await
            .map_err(Into::into)
    } else {
        let part = reqwest::multipart::Part::bytes(nzb_bytes)
            .file_name(filename)
            .mime_str("application/x-nzb")
            .map_err(|e| ProviderError::Other(format!("TorBox: mime error: {e}")))?;
        let form = reqwest::multipart::Form::new().part("file", part);

        http.post(&url)
            .bearer_auth(token)
            .multipart(form)
            .send()
            .await?
            .json()
            .await
            .map_err(Into::into)
    }
}

// ─── mylist / find / ready / file-select ──────────────────────────────────────

async fn mylist(
    http: &reqwest::Client,
    token: &str,
    forward: Option<&MediaFlowForward>,
) -> Result<Vec<Value>, ProviderError> {
    let url = format!("{BASE}/usenet/mylist");
    let resp: Value = if let Some(fwd) = forward {
        let dest = append_query(&url, &[("bypass_cache", "true")]);
        fwd.get(http, &dest, token).await?.json().await?
    } else {
        http.get(&url)
            .query(&[("bypass_cache", "true")])
            .bearer_auth(token)
            .send()
            .await?
            .json()
            .await?
    };

    Ok(resp
        .get("data")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default())
}

fn find_item<'a>(list: &'a [Value], name: &str, nzb_guid: &str) -> Option<&'a Value> {
    list.iter().find(|item| {
        if let Some(h) = item.get("hash").and_then(|v| v.as_str()) {
            if h.eq_ignore_ascii_case(nzb_guid) {
                return true;
            }
        }
        item.get("name")
            .and_then(|v| v.as_str())
            .map(|n| loose_name_match(n, name))
            .unwrap_or(false)
    })
}

fn is_ready(item: &Value) -> bool {
    item.get("download_finished")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
        && item
            .get("download_present")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
}

/// Returns true for URLs that TorBox can fetch natively (its own search API).
/// For these, URL submission works even on localhost; for all others we skip
/// straight to file upload to avoid TorBox timing out on third-party URLs.
fn is_torbox_native_url(url: &str) -> bool {
    url.contains("torbox.app") || url.contains("torbox.io")
}

fn select_file(info: &Value, _name: &str, season: i32, episode: i32) -> i64 {
    let files = match info.get("files").and_then(|v| v.as_array()) {
        Some(f) if !f.is_empty() => f,
        _ => return 0,
    };

    if season > 0 && episode > 0 {
        let se = format!("s{:02}e{:02}", season, episode);
        for f in files {
            let fname = f
                .get("name")
                .or_else(|| f.get("short_name"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_lowercase();
            if fname.contains(&se) && is_video_name(&fname) {
                if let Some(id) = f.get("id").and_then(|v| v.as_i64()) {
                    return id;
                }
            }
        }
    }

    // Largest video file
    files
        .iter()
        .filter(|f| {
            is_video_name(
                f.get("name")
                    .or_else(|| f.get("short_name"))
                    .and_then(|v| v.as_str())
                    .unwrap_or(""),
            )
        })
        .max_by_key(|f| f.get("size").and_then(|v| v.as_i64()).unwrap_or(0))
        .and_then(|f| f.get("id").and_then(|v| v.as_i64()))
        .unwrap_or(0)
}
