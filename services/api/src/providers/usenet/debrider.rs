/// Debrider usenet playback provider.
///
/// Flow (instant — no polling):
///   POST /link/generate → files array → select best video file → download_link
use serde_json::Value;

use crate::providers::ProviderError;

use super::select_best_file;

const BASE: &str = "https://debrider.app/api/v1";

pub async fn get_url(
    http: &reqwest::Client,
    token: &str,
    nzb_url: &str,
    _name: &str,
    season: i32,
    episode: i32,
) -> Result<String, ProviderError> {
    if token.is_empty() {
        return Err(ProviderError::api(
            "Debrider: no API token configured",
            "invalid_token.mp4",
        ));
    }
    if nzb_url.is_empty() {
        return Err(ProviderError::api(
            "Debrider: no NZB URL",
            "stream_not_found.mp4",
        ));
    }

    let resp: Value = http
        .post(format!("{BASE}/link/generate"))
        .bearer_auth(token)
        .json(&serde_json::json!({"data": nzb_url}))
        .send()
        .await?
        .json()
        .await?;

    let files = resp
        .get("files")
        .and_then(|v| v.as_array())
        .filter(|a| !a.is_empty())
        .ok_or_else(|| {
            ProviderError::api(
                format!("Debrider: no files in response: {resp}"),
                "no_video_file_found.mp4",
            )
        })?;

    let best = select_best_file(files, season, episode).ok_or_else(|| {
        ProviderError::api(
            "Debrider: no suitable video file",
            "no_video_file_found.mp4",
        )
    })?;

    best.get("download_link")
        .or_else(|| best.get("url"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            ProviderError::api(
                "Debrider: file has no download_link",
                "usenet_transfer_error.mp4",
            )
        })
}
