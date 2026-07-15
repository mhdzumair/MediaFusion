/// Newznab NZB indexer scraper.
///
/// User-configured Newznab indexers return NZB releases (no torrent infohash).
/// Results are mapped to [`ScrapedUsenetStream`] for the usenet pipeline.
use std::collections::HashSet;

use reqwest::Client;
use sha2::{Digest, Sha256};

use crate::{
    models::user_data::NewznabIndexer,
    parser,
    providers::usenet::nzb_url::sanitize_nzb_url,
    scrapers::{ScrapedUsenetStream, SearchMeta, prowlarr::build_series_files},
    state::KeywordFilterCache,
};

const MOVIE_CATS: &[i64] = &[2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070];
const TV_CATS: &[i64] = &[5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070];

/// Normalize a Newznab indexer base URL to its API endpoint, avoiding duplicate `/api`.
pub fn build_newznab_api_url(url: &str) -> String {
    let raw_url = url.trim().trim_end_matches('/');
    if raw_url.is_empty() {
        return String::new();
    }

    if let Ok(mut parsed) = url::Url::parse(raw_url) {
        if parsed.scheme().is_empty() || parsed.host_str().is_none() {
            return if raw_url.ends_with("/api") {
                raw_url.to_string()
            } else {
                format!("{raw_url}/api")
            };
        }

        let path = parsed.path().trim_end_matches('/');
        let api_path = if path.ends_with("/api") {
            path.to_string()
        } else if path.is_empty() {
            "/api".to_string()
        } else {
            format!("{path}/api")
        };
        parsed.set_path(&api_path);
        parsed.set_query(None);
        parsed.set_fragment(None);
        return parsed.to_string();
    }

    if raw_url.ends_with("/api") {
        raw_url.to_string()
    } else {
        format!("{raw_url}/api")
    }
}

/// Scrape all enabled user-configured Newznab indexers.
pub async fn scrape(
    client: &Client,
    indexers: &[NewznabIndexer],
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedUsenetStream> {
    let enabled: Vec<_> = indexers.iter().filter(|idx| idx.enabled).collect();
    if enabled.is_empty() {
        return Vec::new();
    }

    let mut handles = Vec::with_capacity(enabled.len());
    for idx in enabled {
        let client = client.clone();
        let idx = (*idx).clone();
        let meta = meta.clone();
        let mt = media_type.to_string();
        let kf = keyword_filters.clone();
        handles.push(tokio::spawn(async move {
            scrape_indexer(&client, &idx, &meta, &mt, season, episode, &kf).await
        }));
    }

    let mut all = Vec::new();
    let mut seen_guids = HashSet::new();
    for h in handles {
        match h.await {
            Ok(streams) => {
                for s in streams {
                    if seen_guids.insert(s.nzb_guid.clone()) {
                        all.push(s);
                    }
                }
            }
            Err(e) => tracing::debug!("newznab indexer task panicked: {e}"),
        }
    }
    all
}

async fn scrape_indexer(
    client: &Client,
    indexer: &NewznabIndexer,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedUsenetStream> {
    let api_url = build_newznab_api_url(&indexer.url);
    if api_url.is_empty() {
        return Vec::new();
    }

    let cats: Vec<i64> = if media_type == "series" {
        if indexer.tv_categories.is_empty() {
            TV_CATS.to_vec()
        } else {
            indexer.tv_categories.clone()
        }
    } else if indexer.movie_categories.is_empty() {
        MOVIE_CATS.to_vec()
    } else {
        indexer.movie_categories.clone()
    };
    let cat_str = cats
        .iter()
        .map(|c| c.to_string())
        .collect::<Vec<_>>()
        .join(",");

    let mut results = Vec::new();
    let mut seen = HashSet::new();

    let search_sets: Vec<Vec<(String, String)>> = if media_type == "series" {
        let mut sets = Vec::new();
        if let Some(id) = meta.imdb_id.as_deref().filter(|s| !s.is_empty()) {
            let mut params = vec![
                ("t".into(), "tvsearch".into()),
                ("imdbid".into(), id.trim_start_matches("tt").to_string()),
                ("cat".into(), cat_str.clone()),
            ];
            if let Some(s) = season {
                params.push(("season".into(), s.to_string()));
            }
            if let Some(e) = episode {
                params.push(("ep".into(), e.to_string()));
            }
            sets.push(params);
        }
        let q = match (season, episode) {
            (Some(s), Some(e)) => format!("{} S{s:02}E{e:02}", meta.title),
            (Some(s), None) => format!("{} S{s:02}", meta.title),
            _ => meta.title.clone(),
        };
        sets.push(vec![
            ("t".into(), "search".into()),
            ("q".into(), q),
            ("cat".into(), cat_str),
        ]);
        sets
    } else {
        let mut sets = Vec::new();
        if let Some(id) = meta.imdb_id.as_deref().filter(|s| !s.is_empty()) {
            sets.push(vec![
                ("t".into(), "movie".into()),
                ("imdbid".into(), id.trim_start_matches("tt").to_string()),
                ("cat".into(), cat_str.clone()),
            ]);
        }
        let q = if let Some(y) = meta.year {
            format!("{} {y}", meta.title)
        } else {
            meta.title.clone()
        };
        sets.push(vec![
            ("t".into(), "search".into()),
            ("q".into(), q),
            ("cat".into(), cat_str),
        ]);
        sets
    };

    for params in search_sets {
        let items = fetch_items(client, &api_url, indexer.api_key.as_deref(), &params).await;
        for item in items {
            if let Some(stream) = finalize_item(
                item,
                &api_url,
                &indexer.url,
                &indexer.name,
                media_type,
                season,
                episode,
                keyword_filters,
            ) && seen.insert(stream.nzb_guid.clone())
            {
                results.push(stream);
            }
        }
    }

    results
}

async fn fetch_items(
    client: &Client,
    api_url: &str,
    api_key: Option<&str>,
    params: &[(String, String)],
) -> Vec<XmlItem> {
    let mut req = client
        .get(api_url)
        .timeout(std::time::Duration::from_secs(30));
    for (k, v) in params {
        req = req.query(&[(k.as_str(), v.as_str())]);
    }
    if let Some(key) = api_key.filter(|k| !k.is_empty()) {
        req = req.query(&[("apikey", key)]);
    }

    let text = match req.send().await {
        Ok(r) => match r.text().await {
            Ok(t) => t,
            Err(e) => {
                tracing::debug!("newznab: body read failed: {e}");
                return vec![];
            }
        },
        Err(e) => {
            tracing::debug!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "newznab: request failed: {e}"
            );
            return vec![];
        }
    };

    parse_xml(&text)
}

struct XmlItem {
    title: Option<String>,
    link: Option<String>,
    guid: Option<String>,
    enclosure_url: Option<String>,
    enclosure_length: Option<i64>,
    attr_size: Option<i64>,
    group_name: Option<String>,
}

impl XmlItem {
    fn new() -> Self {
        Self {
            title: None,
            link: None,
            guid: None,
            enclosure_url: None,
            enclosure_length: None,
            attr_size: None,
            group_name: None,
        }
    }
}

fn parse_xml(xml: &str) -> Vec<XmlItem> {
    use quick_xml::Reader;
    use quick_xml::events::Event;

    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);

    let mut buf = Vec::new();
    let mut results = Vec::new();
    let mut in_item = false;
    let mut current = XmlItem::new();
    let mut current_text_field: Option<&'static str> = None;

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => match e.local_name().as_ref() {
                b"item" => {
                    in_item = true;
                    current = XmlItem::new();
                }
                b"title" if in_item => current_text_field = Some("title"),
                b"link" if in_item => current_text_field = Some("link"),
                b"guid" if in_item => current_text_field = Some("guid"),
                _ => {}
            },
            Ok(Event::Empty(e)) if in_item => match e.local_name().as_ref() {
                b"attr" => {
                    let mut name = None::<String>;
                    let mut value = None::<String>;
                    for attr in e.attributes().flatten() {
                        match attr.key.local_name().as_ref() {
                            b"name" => {
                                name = attr
                                    .normalized_value(quick_xml::XmlVersion::Implicit1_0)
                                    .ok()
                                    .map(|v| v.into_owned());
                            }
                            b"value" => {
                                value = attr
                                    .normalized_value(quick_xml::XmlVersion::Implicit1_0)
                                    .ok()
                                    .map(|v| v.into_owned());
                            }
                            _ => {}
                        }
                    }
                    match (name.as_deref(), value) {
                        (Some("size"), Some(v)) => current.attr_size = v.parse().ok(),
                        (Some("group"), Some(v)) => current.group_name = Some(v),
                        _ => {}
                    }
                }
                b"enclosure" => {
                    for attr in e.attributes().flatten() {
                        match attr.key.local_name().as_ref() {
                            b"length" => {
                                current.enclosure_length = attr
                                    .normalized_value(quick_xml::XmlVersion::Implicit1_0)
                                    .ok()
                                    .and_then(|v| v.parse().ok());
                            }
                            b"url" => {
                                current.enclosure_url = attr
                                    .normalized_value(quick_xml::XmlVersion::Implicit1_0)
                                    .ok()
                                    .map(|v| v.into_owned());
                            }
                            _ => {}
                        }
                    }
                }
                _ => {}
            },
            Ok(Event::Text(e)) => {
                if let Some(field) = current_text_field.take() {
                    let text = e.decode().unwrap_or_default().into_owned();
                    match field {
                        "title" => current.title = Some(text),
                        "link" => current.link = Some(text),
                        "guid" => current.guid = Some(text),
                        _ => {}
                    }
                }
            }
            Ok(Event::End(e)) if e.local_name().as_ref() == b"item" && in_item => {
                in_item = false;
                current_text_field = None;
                if current.title.as_deref().is_some_and(|t| !t.is_empty()) {
                    results.push(current);
                }
                current = XmlItem::new();
            }
            Ok(Event::Eof) => break,
            Err(e) => {
                tracing::debug!("newznab xml parse error: {e}");
                break;
            }
            _ => {}
        }
        buf.clear();
    }

    results
}

fn nzb_guid(indexer_url: &str, guid: &str) -> String {
    let digest = Sha256::digest(format!("{indexer_url}:{guid}").as_bytes());
    digest[..20].iter().map(|b| format!("{b:02x}")).collect()
}

fn extract_link_id(link: &str) -> Option<String> {
    url::Url::parse(link)
        .ok()
        .and_then(|u| {
            u.query_pairs()
                .find(|(k, _)| k == "id")
                .map(|(_, v)| v.into_owned())
        })
        .filter(|s| !s.is_empty())
}

fn build_download_url(api_url: &str, download_id: &str) -> Option<String> {
    sanitize_nzb_url(&format!(
        "{api_url}?t=get&id={}",
        urlencoding::encode(download_id)
    ))
}

fn finalize_item(
    item: XmlItem,
    api_url: &str,
    indexer_url: &str,
    source: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Option<ScrapedUsenetStream> {
    let title = item.title?.trim().to_string();
    if title.is_empty() || keyword_filters.matches_blocked_keyword(&title) {
        return None;
    }

    let guid = item.guid.as_deref().unwrap_or("").trim();
    if guid.is_empty() {
        return None;
    }

    let download_id = item
        .link
        .as_deref()
        .and_then(extract_link_id)
        .unwrap_or_else(|| guid.to_string());

    let nzb_url = build_download_url(api_url, &download_id).or_else(|| {
        item.enclosure_url
            .as_deref()
            .and_then(sanitize_nzb_url)
            .or_else(|| item.link.as_deref().and_then(sanitize_nzb_url))
    })?;

    let size = item.attr_size.or(item.enclosure_length).unwrap_or(0);
    let parsed = parser::parse_title(&title);
    let files = if media_type == "series" {
        build_series_files(&parsed, season, episode)
    } else {
        vec![]
    };

    Some(ScrapedUsenetStream {
        nzb_guid: nzb_guid(indexer_url, guid),
        nzb_url,
        name: title,
        size,
        indexer: source.to_string(),
        source: source.to_string(),
        group_name: item.group_name,
        parsed,
        files,
        is_cached: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_api_url_appends_api_when_missing() {
        assert_eq!(
            build_newznab_api_url("https://drunkenslug.com"),
            "https://drunkenslug.com/api"
        );
    }

    #[test]
    fn build_api_url_avoids_duplicate_api() {
        assert_eq!(
            build_newznab_api_url("https://drunkenslug.com/api"),
            "https://drunkenslug.com/api"
        );
    }

    #[test]
    fn build_api_url_strips_existing_query() {
        assert_eq!(
            build_newznab_api_url("https://drunkenslug.com/api?apikey=old"),
            "https://drunkenslug.com/api"
        );
    }
}
