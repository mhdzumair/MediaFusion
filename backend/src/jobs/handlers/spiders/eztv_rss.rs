use async_trait::async_trait;
use quick_xml::{Reader, events::Event};
use tracing::{debug, info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{ScrapedStream, media_resolve, prowlarr::build_series_files, stream_convert},
    util::rate_limit,
};

pub struct EztvRssCrawl;

const FEED_URL: &str = "https://eztv.re/ezrss.xml";

// ─── XML parsing ──────────────────────────────────────────────────────────────

#[derive(Default)]
pub struct RssItem {
    pub title: String,
    pub enclosure_url: Option<String>,
    pub enclosure_size: Option<i64>,
    pub info_hash: Option<String>,
    pub seeds: Option<i32>,
}

pub fn parse_eztv_rss(xml: &str) -> Vec<RssItem> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);

    let mut items: Vec<RssItem> = Vec::new();
    let mut current: Option<RssItem> = None;
    let mut buf = Vec::new();
    // Track which text-bearing element we are inside
    let mut in_title = false;
    let mut in_info_hash = false;
    let mut in_seeds = false;

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                match name.as_str() {
                    "item" => {
                        current = Some(RssItem::default());
                    }
                    "title" if current.is_some() => {
                        in_title = true;
                    }
                    // quick_xml preserves the namespace prefix as-is: "torrent:infoHash"
                    "torrent:infoHash" | "infoHash" if current.is_some() => {
                        in_info_hash = true;
                    }
                    "torrent:seeds" | "seeds" if current.is_some() => {
                        in_seeds = true;
                    }
                    _ => {}
                }
            }
            Ok(Event::Empty(ref e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                if name == "enclosure"
                    && let Some(ref mut item) = current {
                        let mut url: Option<String> = None;
                        let mut length: Option<i64> = None;
                        for attr in e.attributes().flatten() {
                            let key =
                                String::from_utf8_lossy(attr.key.local_name().as_ref()).to_string();
                            let val = String::from_utf8_lossy(&attr.value).to_string();
                            match key.as_str() {
                                "url" => url = Some(val),
                                "length" => length = val.parse::<i64>().ok(),
                                _ => {}
                            }
                        }
                        item.enclosure_url = url;
                        item.enclosure_size = length;
                    }
            }
            Ok(Event::Text(ref e)) => {
                let text = e.decode().unwrap_or_default().trim().to_string();
                if !text.is_empty()
                    && let Some(ref mut item) = current {
                        if in_title {
                            item.title = text;
                        } else if in_info_hash {
                            item.info_hash = Some(text);
                        } else if in_seeds {
                            item.seeds = text.parse::<i32>().ok();
                        }
                    }
                in_title = false;
                in_info_hash = false;
                in_seeds = false;
            }
            Ok(Event::End(ref e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                match name.as_str() {
                    "item" => {
                        if let Some(item) = current.take() {
                            items.push(item);
                        }
                    }
                    "title" => in_title = false,
                    "torrent:infoHash" | "infoHash" => in_info_hash = false,
                    "torrent:seeds" | "seeds" => in_seeds = false,
                    _ => {}
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => {
                warn!("eztv_rss: XML parse error: {e}");
                break;
            }
            _ => {}
        }
        buf.clear();
    }

    items
}

// ─── Job handler ──────────────────────────────────────────────────────────────

#[async_trait]
impl JobHandler for EztvRssCrawl {
    const QUEUE: &'static str = "spider_eztv_rss";
    const CONCURRENCY: usize = 2;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        // Rate-limit before the main fetch
        rate_limit::wait("eztv.re", 5).await;

        let client = &ctx.state.http;
        let pool = &ctx.state.pool;

        // Fetch RSS feed
        let text = match client
            .get(FEED_URL)
            .timeout(std::time::Duration::from_secs(30))
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => match r.text().await {
                Ok(t) => t,
                Err(e) => {
                    warn!("eztv_rss: failed to read body: {e}");
                    return Ok(());
                }
            },
            Ok(r) => {
                warn!("eztv_rss: HTTP {}", r.status());
                return Ok(());
            }
            Err(e) => {
                warn!("eztv_rss: fetch error: {e}");
                return Ok(());
            }
        };

        let rss_items = parse_eztv_rss(&text);
        info!("eztv_rss: parsed {} items from feed", rss_items.len());

        if rss_items.is_empty() {
            return Ok(());
        }

        let kf = ctx
            .state
            .keyword_filters
            .read()
            .map(|g| g.clone())
            .unwrap_or_default();

        for item in &rss_items {
            if ctx.cancel.is_cancelled() {
                debug!("eztv_rss: cancelled during item processing");
                return Err(JobError::Cancelled);
            }

            let info_hash = match &item.info_hash {
                Some(h) if h.len() == 40 => h.to_lowercase(),
                Some(_) => {
                    // Try extracting from enclosure URL (magnet)
                    match item
                        .enclosure_url
                        .as_deref()
                        .and_then(parser::extract_info_hash)
                    {
                        Some(extracted) => extracted.to_lowercase(),
                        None => {
                            debug!(
                                "eztv_rss: skipping item with no extractable hash: {}",
                                item.title
                            );
                            continue;
                        }
                    }
                }
                None => {
                    // Try extracting from enclosure magnet URL
                    match item
                        .enclosure_url
                        .as_deref()
                        .and_then(parser::extract_info_hash)
                    {
                        Some(h) => h.to_lowercase(),
                        None => {
                            debug!("eztv_rss: skipping item with no hash: {}", item.title);
                            continue;
                        }
                    }
                }
            };

            let title = item.title.trim().to_string();
            if title.is_empty() {
                continue;
            }

            if kf.matches_blocked_keyword(&title) {
                continue;
            }

            // Rate-limit per item
            rate_limit::wait("eztv.re", 5).await;

            let parsed = parser::parse_title(&title);
            let is_series = !parsed.seasons.is_empty() || !parsed.episodes.is_empty();
            let media_type = if is_series { "series" } else { "movie" };
            let files = if is_series {
                build_series_files(&parsed, None, None)
            } else {
                vec![]
            };

            let stream = ScrapedStream {
                info_hash,
                name: title,
                source: "EZTV".to_string(),
                seeders: item.seeds,
                size: item.enclosure_size,
                parsed,
                files,
                is_cached: false,
                torrent_type: crate::db::TorrentType::Public,
                torrent_file: None,
                announce_list: vec![],
            };

            let cfg = &ctx.state.config;
            if let Some(meta) = media_resolve::search_meta_for_scraped(
                pool,
                &ctx.state.http,
                &stream,
                is_series,
                cfg.tmdb_api_key.as_deref(),
                cfg.imdb_cinemeta_fallback_enabled,
                &cfg.anime_metadata_source_order,
                &cfg.metadata_primary_source,
            )
            .await
            {
                stream_convert::write_back_torrents(
                    pool,
                    std::slice::from_ref(&stream),
                    &meta,
                    media_type,
                    None,
                    None,
                )
                .await;
            }
        }

        Ok(())
    }
}
