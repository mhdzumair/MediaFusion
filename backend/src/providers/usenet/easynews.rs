/// EasyNews usenet playback provider.
///
/// Flow (server-side, instant — no download queue):
///   1. POST /2.0/search/solr-search/advanced with user's credentials
///   2. Select best result by season/episode or Jaccard similarity + size
///   3. Build streaming URL with embedded `user:pass@` credentials
use serde_json::Value;

use base64::{engine::general_purpose::STANDARD as B64, Engine as _};

use crate::providers::{torrents::transport::MediaFlowForward, ProviderError};

use super::{clean_for_search, is_video_ext, jaccard_similarity};

pub async fn get_url(
    http: &reqwest::Client,
    username: &str,
    password: &str,
    name: &str,
    season: i32,
    episode: i32,
    forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    if username.is_empty() || password.is_empty() {
        return Err(ProviderError::api(
            "EasyNews: no credentials configured",
            "invalid_credentials.mp4",
        ));
    }

    let query = clean_for_search(name);
    let search_params = [
        ("st", "adv"),
        ("sb", "1"),
        ("fex", "mkv,mp4,avi,mov,m4v"),
        ("gps", query.as_str()),
        ("pby", "50"),
        ("s1", "nrr1"),
        ("s1d", "-"),
        ("s2", "vd"),
        ("s2d", "-"),
        ("s3", "nrr"),
        ("s3d", "-"),
    ];
    let base_url = "https://members.easynews.com/2.0/search/solr-search/advanced";

    let resp: Value = if let Some(fwd) = forward {
        use crate::providers::torrents::transport::append_query;
        let dest = append_query(
            base_url,
            &search_params
                .iter()
                .map(|(k, v)| (*k, *v))
                .collect::<Vec<_>>(),
        );
        let auth = format!("Basic {}", B64.encode(format!("{username}:{password}")));
        fwd.get_auth(http, &dest, &auth).await?.json().await?
    } else {
        http.get(base_url)
            .query(&search_params)
            .basic_auth(username, Some(password))
            .send()
            .await?
            .json()
            .await?
    };

    let down_url = resp
        .get("downURL")
        .and_then(|v| v.as_str())
        .unwrap_or("members.easynews.com");
    let dl_farm = resp.get("dlFarm").and_then(|v| v.as_str()).unwrap_or("dl");
    let dl_port = resp
        .get("dlPort")
        .and_then(|v| v.as_str())
        .unwrap_or_default();

    let results = resp
        .get("data")
        .and_then(|v| v.as_array())
        .filter(|a| !a.is_empty())
        .ok_or_else(|| {
            ProviderError::api("EasyNews: no results for query", "no_video_file_found.mp4")
        })?;

    let best = select_result(results, name, season, episode).ok_or_else(|| {
        ProviderError::api(
            "EasyNews: no matching video result",
            "no_video_file_found.mp4",
        )
    })?;

    build_url(best, down_url, dl_farm, dl_port, username, password)
}

// ─── Internal helpers ──────────────────────────────────────────────────────────

fn select_result<'a>(
    results: &'a [Value],
    name: &str,
    season: i32,
    episode: i32,
) -> Option<&'a Value> {
    // EasyNews result fields: 2=subject, 10=title, 11=extension
    if season > 0 && episode > 0 {
        let se = format!("s{:02}e{:02}", season, episode);
        for r in results {
            let subj = subject(r).to_lowercase();
            if subj.contains(&se)
                && is_video_ext(r.get("11").and_then(|v| v.as_str()).unwrap_or(""))
            {
                return Some(r);
            }
        }
    }

    let name_lc = name.to_lowercase();
    results
        .iter()
        .filter(|r| is_video_ext(r.get("11").and_then(|v| v.as_str()).unwrap_or("")))
        .max_by_key(|r| {
            let sim = (jaccard_similarity(&subject(r).to_lowercase(), &name_lc) * 1000.0) as i64;
            let size = r.get("4").and_then(|v| v.as_i64()).unwrap_or(0);
            sim * 100_000_000 + size / 1_000_000
        })
}

fn subject(r: &Value) -> String {
    r.get("2")
        .or_else(|| r.get("10"))
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string()
}

fn build_url(
    result: &Value,
    down_url: &str,
    dl_farm: &str,
    dl_port: &str,
    username: &str,
    password: &str,
) -> Result<String, ProviderError> {
    let file_hash = result.get("hash").and_then(|v| v.as_str()).unwrap_or("");
    let title = result
        .get("10")
        .or_else(|| result.get("2"))
        .and_then(|v| v.as_str())
        .unwrap_or_default();
    let ext = result.get("11").and_then(|v| v.as_str()).unwrap_or("mkv");

    let enc_u = urlencoding::encode(username);
    let enc_p = urlencoding::encode(password);

    if !file_hash.is_empty() {
        let port_seg = if dl_port.is_empty() {
            String::new()
        } else {
            format!("/{dl_port}")
        };
        let enc_title = urlencoding::encode(title);
        Ok(format!(
            "https://{enc_u}:{enc_p}@{down_url}/{dl_farm}{port_seg}/{file_hash}.{ext}/{enc_title}.{ext}"
        ))
    } else {
        // Fallback: direct link via signature
        let sig = result.get("sig").and_then(|v| v.as_str()).unwrap_or("");
        let file_id = result.get("0").and_then(|v| v.as_str()).unwrap_or("");
        let subj = result.get("2").and_then(|v| v.as_str()).unwrap_or_default();
        if sig.is_empty() {
            return Err(ProviderError::api(
                "EasyNews: result has neither hash nor sig",
                "usenet_transfer_error.mp4",
            ));
        }
        Ok(format!(
            "https://{enc_u}:{enc_p}@members.easynews.com/dl/{file_id}/{subj}?sig={sig}"
        ))
    }
}
