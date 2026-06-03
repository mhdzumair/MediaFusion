/// Usenet streaming provider implementations.
///
/// Each sub-module owns a single provider's playback logic. Common helpers
/// (file selection, name matching) live here so all providers share them.
pub mod cache;
pub mod debrider;
pub mod easynews;
pub mod mgmt;
pub mod nzb_url;
pub mod nzbdav;
pub mod nzbget;
pub mod sabnzbd;
pub mod torbox;
pub mod webdav;

// ─── NZB fetch fallback ────────────────────────────────────────────────────────

/// Download the raw NZB file bytes from `url`.
///
/// Used as a fallback by every provider: when the provider cannot fetch the NZB
/// URL itself (e.g. because the Newznab indexer requires auth credentials that
/// the provider doesn't have), we download it server-side using the resolved URL
/// (which already carries the user's API key) and upload the bytes directly.
pub async fn fetch_nzb_bytes(
    http: &reqwest::Client,
    url: &str,
) -> Result<Vec<u8>, crate::providers::ProviderError> {
    tracing::debug!("fetch_nzb_bytes: GET {url}");
    let resp = http.get(url).send().await?;
    let status = resp.status();
    let content_type = resp
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    tracing::debug!("fetch_nzb_bytes: status={status} content-type={content_type}");
    if !status.is_success() {
        return Err(crate::providers::ProviderError::api(
            format!("failed to fetch NZB file (HTTP {status}) from indexer"),
            "usenet_transfer_error.mp4",
        ));
    }
    let bytes = resp
        .bytes()
        .await
        .map_err(crate::providers::ProviderError::Http)?;
    tracing::debug!("fetch_nzb_bytes: got {} bytes", bytes.len());
    Ok(bytes.to_vec())
}

// ─── Common video-file helpers ─────────────────────────────────────────────────

pub fn is_video_name(name: &str) -> bool {
    let lower = name.to_lowercase();
    lower.ends_with(".mkv")
        || lower.ends_with(".mp4")
        || lower.ends_with(".avi")
        || lower.ends_with(".mov")
        || lower.ends_with(".m4v")
        || lower.ends_with(".wmv")
        || lower.ends_with(".ts")
        || lower.ends_with(".m2ts")
}

pub fn is_video_ext(ext: &str) -> bool {
    matches!(
        ext.to_lowercase().as_str(),
        "mkv" | "mp4" | "avi" | "mov" | "m4v" | "wmv" | "ts" | "m2ts"
    )
}

/// Case-insensitive, punctuation-collapsed name match (both-way substring).
pub fn loose_name_match(a: &str, b: &str) -> bool {
    let norm = |s: &str| s.to_lowercase().replace(['.', '-', '_', ' '], "");
    let na = norm(a);
    let nb = norm(b);
    na.contains(&nb) || nb.contains(&na)
}

/// Strip quality/codec tags so a stream name can be used as a search query.
pub fn clean_for_search(name: &str) -> String {
    const STRIP: &[&str] = &[
        "2160p", "1080p", "720p", "480p", "4k", "uhd", "hdr", "dv", "hevc", "h264", "h265", "avc",
        "x264", "x265", "xvid", "bluray", "blu-ray", "bdrip", "web-dl", "webrip", "hdrip", "10bit",
        "8bit", "aac", "ac3", "dts", "atmos",
    ];
    let mut s = name.to_lowercase();
    for pat in STRIP {
        s = s.replace(pat, " ");
    }
    s.split_whitespace()
        .filter(|w| w.len() > 1)
        .collect::<Vec<_>>()
        .join(" ")
}

/// Jaccard similarity over whitespace-tokenised words.
pub fn jaccard_similarity(a: &str, b: &str) -> f64 {
    let aw: std::collections::HashSet<&str> = a.split_whitespace().collect();
    let bw: std::collections::HashSet<&str> = b.split_whitespace().collect();
    if aw.is_empty() && bw.is_empty() {
        return 1.0;
    }
    let common = aw.intersection(&bw).count();
    let union = aw.union(&bw).count();
    if union == 0 {
        0.0
    } else {
        common as f64 / union as f64
    }
}

/// Select the best file from a JSON array by season/episode pattern, then largest video.
pub fn select_best_file(
    files: &[serde_json::Value],
    season: i32,
    episode: i32,
) -> Option<&serde_json::Value> {
    if season > 0 && episode > 0 {
        let se = format!("s{:02}e{:02}", season, episode);
        for f in files {
            let fname = file_name(f).to_lowercase();
            if fname.contains(&se) && is_video_name(&fname) {
                return Some(f);
            }
        }
    }
    files
        .iter()
        .filter(|f| is_video_name(&file_name(f)))
        .max_by_key(|f| f.get("size").and_then(|v| v.as_i64()).unwrap_or(0))
}

fn file_name(v: &serde_json::Value) -> String {
    v.get("name")
        .or_else(|| v.get("filename"))
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string()
}
