/// Debrider torrent streaming provider.
///
/// Flow:
///   POST /link/generate with magnet → files[] → select best → download_link
use serde_json::Value;

use crate::providers::{
    file_selection::{files_from_json, select_torrent_file_index, FileEntry},
    torrents::transport::MediaFlowForward,
    ProviderError,
};

const BASE: &str = "https://debrider.app/api/v1";

fn auth_headers(
    token: &str,
    user_ip: Option<&str>,
) -> Result<reqwest::header::HeaderMap, ProviderError> {
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert(
        reqwest::header::AUTHORIZATION,
        reqwest::header::HeaderValue::from_str(&format!("Bearer {token}")).map_err(|_| {
            ProviderError::api("Invalid debrider token", "invalid_debrider_token.mp4")
        })?,
    );
    if let Some(ip) = user_ip.filter(|s| !s.is_empty() && *s != "{mediaflow_ip}") {
        headers.insert(
            "X-Forwarded-For",
            reqwest::header::HeaderValue::from_str(ip).map_err(|_| {
                ProviderError::api("Invalid client IP for debrider", "invalid_client_ip.mp4")
            })?,
        );
    }
    Ok(headers)
}

pub async fn validate_credentials(
    http: &reqwest::Client,
    token: &str,
    user_ip: Option<&str>,
) -> Result<(), ProviderError> {
    let resp = http
        .get(format!("{BASE}/account"))
        .headers(auth_headers(token, user_ip)?)
        .send()
        .await?;
    if resp.status().is_success() {
        Ok(())
    } else {
        Err(ProviderError::api(
            "Failed to validate Debrider credentials",
            "invalid_token.mp4",
        ))
    }
}

pub async fn get_video_url(
    http: &reqwest::Client,
    token: &str,
    magnet_link: &str,
    torrent_name: &str,
    filename: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    if token.is_empty() {
        return Err(ProviderError::api(
            "Debrider: no API token configured",
            "invalid_token.mp4",
        ));
    }

    let url = format!("{BASE}/link/generate");
    let body = serde_json::json!({"data": magnet_link}).to_string();

    let info: Value = if let Some(fwd) = forward {
        fwd.post_json(http, &url, token, body).await?.json().await?
    } else {
        http.post(&url)
            .headers(auth_headers(token, user_ip)?)
            .json(&serde_json::json!({"data": magnet_link}))
            .send()
            .await?
            .json()
            .await?
    };

    let files_arr = info
        .get("files")
        .and_then(|v| v.as_array())
        .filter(|a| !a.is_empty())
        .ok_or_else(|| {
            ProviderError::api(
                "Unable to generate Debrider link",
                "torrent_not_downloaded.mp4",
            )
        })?;

    let entries = files_from_json(files_arr, &["name", "filename"], &["size", "bytes"]);
    let idx = select_torrent_file_index(
        &entries,
        torrent_name,
        filename,
        season,
        episode,
        None,
        None,
    )?;
    files_arr[idx]
        .get("download_link")
        .or_else(|| files_arr[idx].get("url"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            ProviderError::api("Debrider: file has no download_link", "transfer_error.mp4")
        })
}

/// Check instant availability via POST /link/lookup (chunks of 50).
pub async fn check_cached(
    http: &reqwest::Client,
    token: &str,
    hashes: &[String],
    user_ip: Option<&str>,
) -> Vec<String> {
    if token.is_empty() || hashes.is_empty() {
        return Vec::new();
    }

    let mut cached = Vec::new();
    for chunk in hashes.chunks(50) {
        let magnets: Vec<String> = chunk
            .iter()
            .map(|h| format!("magnet:?xt=urn:btih:{h}"))
            .collect();
        let headers = match auth_headers(token, user_ip) {
            Ok(h) => h,
            Err(e) => {
                tracing::warn!("debrider check_cached: {e}");
                continue;
            }
        };
        let resp = match http
            .post(format!("{BASE}/link/lookup"))
            .headers(headers)
            .json(&serde_json::json!({"data": magnets}))
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!("debrider check_cached: {e}");
                continue;
            }
        };
        let body: Value = match resp.json().await {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!("debrider check_cached parse: {e}");
                continue;
            }
        };
        let results = body
            .get("result")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        for (i, item) in results.iter().enumerate() {
            if item.get("cached").and_then(|v| v.as_bool()) == Some(true) {
                if let Some(h) = chunk.get(i) {
                    cached.push(h.clone());
                }
            }
        }
    }
    cached
}

/// Return provider files for metadata write-back after playback.
pub fn provider_files_from_info(
    info: &Value,
) -> Vec<crate::providers::torrents::metadata_update::ProviderFile> {
    let Some(arr) = info.get("files").and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    arr.iter()
        .enumerate()
        .filter_map(|(idx, f)| {
            let path = f
                .get("name")
                .or_else(|| f.get("filename"))
                .and_then(|v| v.as_str())?;
            let bytes = f
                .get("size")
                .or_else(|| f.get("bytes"))
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            Some(crate::providers::torrents::metadata_update::ProviderFile {
                file_index: idx as i32,
                path: path.to_string(),
                bytes,
            })
        })
        .collect()
}

/// Convenience wrapper returning (url, files) for playback metadata update.
pub async fn get_video_url_with_files(
    http: &reqwest::Client,
    token: &str,
    magnet_link: &str,
    torrent_name: &str,
    filename: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    user_ip: Option<&str>,
    forward: Option<&MediaFlowForward>,
) -> Result<
    (
        String,
        Vec<crate::providers::torrents::metadata_update::ProviderFile>,
    ),
    ProviderError,
> {
    if token.is_empty() {
        return Err(ProviderError::api(
            "Debrider: no API token configured",
            "invalid_token.mp4",
        ));
    }

    let url = format!("{BASE}/link/generate");
    let info: Value = if let Some(fwd) = forward {
        let body = serde_json::json!({"data": magnet_link}).to_string();
        fwd.post_json(http, &url, token, body).await?.json().await?
    } else {
        http.post(&url)
            .headers(auth_headers(token, user_ip)?)
            .json(&serde_json::json!({"data": magnet_link}))
            .send()
            .await?
            .json()
            .await?
    };

    let files_arr = info
        .get("files")
        .and_then(|v| v.as_array())
        .filter(|a| !a.is_empty())
        .ok_or_else(|| {
            ProviderError::api(
                "Unable to generate Debrider link",
                "torrent_not_downloaded.mp4",
            )
        })?;

    let entries: Vec<FileEntry> =
        files_from_json(files_arr, &["name", "filename"], &["size", "bytes"]);
    let idx = select_torrent_file_index(
        &entries,
        torrent_name,
        filename,
        season,
        episode,
        None,
        None,
    )?;
    let video_url = files_arr[idx]
        .get("download_link")
        .or_else(|| files_arr[idx].get("url"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            ProviderError::api("Debrider: file has no download_link", "transfer_error.mp4")
        })?;

    Ok((video_url, provider_files_from_info(&info)))
}
