use reqwest::Client;

use crate::{
    models::user_data::TorznabEndpoint,
    parser,
    scrapers::{prowlarr::build_series_files, ScrapedStream, SearchMeta},
};

const MOVIE_CATS: &[i64] = &[2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070];
const TV_CATS: &[i64] = &[5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070];

/// Scrape all enabled user-configured Torznab endpoints concurrently.
pub async fn scrape(
    client: &Client,
    endpoints: &[TorznabEndpoint],
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedStream> {
    let mut handles = Vec::with_capacity(endpoints.len());

    for ep in endpoints {
        let client = client.clone();
        let ep = ep.clone();
        let meta_title = meta.title.clone();
        let meta_year = meta.year;
        let imdb_id = meta.imdb_id.clone();
        let mt = media_type.to_string();

        handles.push(tokio::spawn(async move {
            query_endpoint(
                &client,
                &ep,
                &meta_title,
                meta_year,
                imdb_id.as_deref(),
                &mt,
                season,
                episode,
            )
            .await
        }));
    }

    let mut all = Vec::new();
    for h in handles {
        match h.await {
            Ok(streams) => all.extend(streams),
            Err(e) => tracing::debug!("torznab endpoint task panicked: {e}"),
        }
    }
    all
}

#[allow(clippy::too_many_arguments)]
async fn query_endpoint(
    client: &Client,
    ep: &TorznabEndpoint,
    title: &str,
    year: Option<i32>,
    imdb_id: Option<&str>,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedStream> {
    let is_series = media_type == "series";

    let cats: Vec<i64> = if ep.categories.is_empty() {
        if is_series {
            TV_CATS.to_vec()
        } else {
            MOVIE_CATS.to_vec()
        }
    } else {
        ep.categories.clone()
    };

    let cat_str: String = cats
        .iter()
        .map(|c| c.to_string())
        .collect::<Vec<_>>()
        .join(",");

    let mut params: Vec<(String, String)> = vec![("cat".into(), cat_str)];

    if let Some(id) = imdb_id.filter(|s| !s.is_empty()) {
        params.push(("imdbid".into(), id.to_string()));
        if is_series {
            params.push(("t".into(), "tvsearch".into()));
            if let Some(s) = season {
                params.push(("season".into(), s.to_string()));
            }
            if let Some(e) = episode {
                params.push(("ep".into(), e.to_string()));
            }
        } else {
            params.push(("t".into(), "movie".into()));
        }
    } else {
        params.push(("t".into(), "search".into()));
        let q = if is_series {
            match (season, episode) {
                (Some(s), Some(e)) => format!("{title} S{s:02}E{e:02}"),
                (Some(s), None) => format!("{title} S{s:02}"),
                _ => title.to_string(),
            }
        } else if let Some(y) = year {
            format!("{title} {y}")
        } else {
            title.to_string()
        };
        params.push(("q".into(), q));
    }

    // Add custom headers from endpoint config
    let mut req = client
        .get(&ep.url)
        .query(&params)
        .timeout(std::time::Duration::from_secs(30));
    if let Some(hdrs) = &ep.headers {
        for (k, v) in hdrs {
            req = req.header(k.as_str(), v.as_str());
        }
    }

    let text = match req.send().await {
        Ok(r) => match r.text().await {
            Ok(t) => t,
            Err(e) => {
                tracing::debug!("torznab {}: body read failed: {e}", ep.name);
                return vec![];
            }
        },
        Err(e) => {
            tracing::debug!("torznab {}: request failed: {e}", ep.name);
            return vec![];
        }
    };

    parse_xml(&text, &ep.name, media_type, season, episode)
}

// ─── XML parser ───────────────────────────────────────────────────────────────

struct XmlItem {
    title: Option<String>,
    link: Option<String>,
    enclosure_length: Option<i64>,
    info_hash: Option<String>,
    magnet_url: Option<String>,
    seeders: Option<i32>,
    size: Option<i64>,
}

impl XmlItem {
    fn new() -> Self {
        XmlItem {
            title: None,
            link: None,
            enclosure_length: None,
            info_hash: None,
            magnet_url: None,
            seeders: None,
            size: None,
        }
    }
}

fn parse_xml(
    xml: &str,
    source: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedStream> {
    use quick_xml::events::Event;
    use quick_xml::Reader;

    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);

    let mut buf = Vec::new();
    let mut results = Vec::new();

    let mut in_item = false;
    let mut current: XmlItem = XmlItem::new();
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
                b"size" if in_item => current_text_field = Some("size"),
                _ => {}
            },
            Ok(Event::Empty(e)) if in_item => {
                // <torznab:attr name="..." value="..."/>  or  <enclosure .../>
                match e.local_name().as_ref() {
                    b"attr" => {
                        let mut name = None::<String>;
                        let mut value = None::<String>;
                        for attr in e.attributes().flatten() {
                            match attr.key.local_name().as_ref() {
                                b"name" => {
                                    name = attr.unescape_value().ok().map(|v| v.into_owned());
                                }
                                b"value" => {
                                    value = attr.unescape_value().ok().map(|v| v.into_owned());
                                }
                                _ => {}
                            }
                        }
                        match (name.as_deref(), value) {
                            (Some("infohash"), Some(v)) => {
                                let h = v.to_lowercase();
                                if h.len() == 40 {
                                    current.info_hash = Some(h);
                                }
                            }
                            (Some("magneturl"), Some(v)) => current.magnet_url = Some(v),
                            (Some("seeders"), Some(v)) => {
                                current.seeders = v.parse().ok();
                            }
                            (Some("size"), Some(v)) => {
                                current.size = v.parse().ok();
                            }
                            _ => {}
                        }
                    }
                    b"enclosure" => {
                        for attr in e.attributes().flatten() {
                            if attr.key.local_name().as_ref() == b"length" {
                                current.enclosure_length =
                                    attr.unescape_value().ok().and_then(|v| v.parse().ok());
                            }
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::Text(e)) => {
                if let Some(field) = current_text_field.take() {
                    let text = e.decode().unwrap_or_default().into_owned();
                    match field {
                        "title" => current.title = Some(text),
                        "link" => current.link = Some(text),
                        "size" => current.size = text.parse().ok(),
                        _ => {}
                    }
                }
            }
            Ok(Event::End(e)) if e.local_name().as_ref() == b"item" && in_item => {
                in_item = false;
                current_text_field = None;
                if let Some(s) = finalize_item(current, source, media_type, season, episode) {
                    results.push(s);
                }
                current = XmlItem::new();
            }
            Ok(Event::Eof) => break,
            Err(e) => {
                tracing::debug!("torznab xml parse error: {e}");
                break;
            }
            _ => {}
        }
        buf.clear();
    }

    results
}

fn finalize_item(
    item: XmlItem,
    source: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<ScrapedStream> {
    let title = item.title?.trim().to_string();
    if title.is_empty() || parser::contains_adult_keywords(&title) {
        return None;
    }

    let info_hash = item
        .info_hash
        .or_else(|| {
            item.magnet_url
                .as_deref()
                .and_then(parser::extract_info_hash)
        })
        .or_else(|| {
            item.link.as_deref().and_then(|l| {
                if l.starts_with("magnet:") {
                    parser::extract_info_hash(l)
                } else {
                    None
                }
            })
        })?;

    let size = item.size.or(item.enclosure_length);
    let parsed = parser::parse_title(&title);
    let files = if media_type == "series" {
        build_series_files(&parsed, season, episode)
    } else {
        vec![]
    };

    Some(ScrapedStream {
        info_hash,
        name: title,
        source: source.to_string(),
        seeders: item.seeders,
        size,
        parsed,
        files,
        is_cached: false,
        torrent_type: crate::db::TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
    })
}
