/// WebDAV helpers shared by SABnzbd, NzbDAV, and NZBGet providers.
///
/// Uses a PROPFIND Depth:1 request to list the download directory, then
/// selects the best-matching video file by season/episode or size.
use quick_xml::{Reader, events::Event};

use crate::providers::ProviderError;

use super::{is_video_name, jaccard_similarity};

/// Issue a WebDAV PROPFIND on `{webdav_base}/{dir_name}` and return all `<href>` values.
pub async fn list(
    http: &reqwest::Client,
    webdav_base: &str,
    dir_name: &str,
    username: &str,
    password: &str,
) -> Result<Vec<String>, ProviderError> {
    let url = if dir_name.is_empty() {
        webdav_base.trim_end_matches('/').to_string()
    } else {
        format!(
            "{}/{}",
            webdav_base.trim_end_matches('/'),
            urlencoding::encode(dir_name)
        )
    };

    let xml = http
        .request(
            reqwest::Method::from_bytes(b"PROPFIND")
                .map_err(|e| ProviderError::api(format!("PROPFIND method: {e}"), "webdav_error.mp4"))?,
            &url,
        )
        .header("Depth", "1")
        .header("Content-Type", "application/xml")
        .basic_auth(username, Some(password))
        .body(
            r#"<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>"#,
        )
        .send()
        .await?
        .text()
        .await?;

    parse_hrefs(&xml)
}

fn parse_hrefs(xml: &str) -> Result<Vec<String>, ProviderError> {
    let mut hrefs: Vec<String> = Vec::new();
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);
    let mut in_href = false;
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) if e.local_name().as_ref() == b"href" => {
                in_href = true;
            }
            Ok(Event::Text(ref e)) if in_href => {
                if let Ok(text) = e.decode() {
                    let s = text.trim().to_string();
                    if !s.is_empty() {
                        hrefs.push(s);
                    }
                }
                in_href = false;
            }
            Ok(Event::End(ref e)) if e.local_name().as_ref() == b"href" => {
                in_href = false;
            }
            Ok(Event::Eof) => break,
            Err(e) => {
                return Err(ProviderError::api(
                    format!("WebDAV XML parse error: {e}"),
                    "webdav_error.mp4",
                ));
            }
            _ => {}
        }
        buf.clear();
    }
    Ok(hrefs)
}

/// Pick the best video file from a WebDAV listing.
///
/// Prefers files matching `S{season}E{episode}`, then falls back to the
/// video file whose path most closely resembles `stream_name` by Jaccard
/// similarity, breaking ties by path length.
pub fn select_video(
    hrefs: &[String],
    stream_name: &str,
    season: i32,
    episode: i32,
) -> Option<String> {
    let videos: Vec<&str> = hrefs
        .iter()
        .map(|h| h.as_str())
        .filter(|h| is_video_name(h))
        .collect();

    if videos.is_empty() {
        return None;
    }

    if season > 0 && episode > 0 {
        let se = format!("s{:02}e{:02}", season, episode);
        if let Some(v) = videos.iter().find(|v| v.to_lowercase().contains(&se)) {
            return Some((*v).to_string());
        }
    }

    let name_lc = stream_name.to_lowercase();
    videos
        .iter()
        .max_by_key(|v| {
            let sim = (jaccard_similarity(&v.to_lowercase(), &name_lc) * 1000.0) as i64;
            (sim, v.len() as i64)
        })
        .map(|s| (*s).to_string())
}

/// Build a WebDAV URL with `user:pass@` embedded in the authority.
pub fn url_with_creds(
    webdav_base: &str,
    file_path: &str,
    username: &str,
    password: &str,
) -> String {
    let enc_u = urlencoding::encode(username);
    let enc_p = urlencoding::encode(password);
    let file = file_path.trim_start_matches('/');
    if let Some(rest) = webdav_base.strip_prefix("https://") {
        format!(
            "https://{enc_u}:{enc_p}@{}/{file}",
            rest.trim_end_matches('/')
        )
    } else if let Some(rest) = webdav_base.strip_prefix("http://") {
        format!(
            "http://{enc_u}:{enc_p}@{}/{file}",
            rest.trim_end_matches('/')
        )
    } else {
        format!("{}/{file}", webdav_base.trim_end_matches('/'))
    }
}
