use async_trait::async_trait;
use scraper::{Html, Selector};
use tracing::{debug, info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{
        fetcher::fetch_plain, media_resolve, prowlarr::build_series_files, stream_convert,
        ScrapedStream,
    },
    util::rate_limit,
};

pub struct ArabTorrentsCrawl;

const SOURCE: &str = "Arab-Torrents";
const DEFAULT_HOMEPAGE: &str = "https://www.arab-torrents.com";

#[derive(Clone)]
struct ForumTarget {
    url: String,
    language: String,
    video_type: String,
}

struct ParsedRow {
    magnet_link: String,
    title: String,
    language: String,
    video_type: String,
    poster_href: Option<String>,
}

#[async_trait]
impl JobHandler for ArabTorrentsCrawl {
    const QUEUE: &'static str = "spider_arab_torrents";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let pages = args
            .get("pages")
            .and_then(|v| v.as_u64())
            .unwrap_or(1)
            .clamp(1, 20) as u32;
        let start_page = args
            .get("start_page")
            .and_then(|v| v.as_u64())
            .unwrap_or(1)
            .max(1) as u32;
        let search_keyword = args
            .get("search_keyword")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        let scrap_catalog_id = args
            .get("scrap_catalog_id")
            .and_then(|v| v.as_str())
            .unwrap_or("all");

        let (homepage, catalogs) = load_spider_config(&ctx.state.config.scraper_config_path);
        let targets = build_targets(
            &homepage,
            &catalogs,
            pages,
            start_page,
            search_keyword.as_deref(),
            scrap_catalog_id,
        );
        if targets.is_empty() {
            warn!("arab_torrents: no crawl targets generated");
            return Ok(());
        }

        info!("arab_torrents: scraping {} forum page(s)", targets.len());
        let client = &ctx.state.http;
        let pool = &ctx.state.pool;
        let domain = rate_limit::domain_key(&homepage);

        let row_sel = Selector::parse("table#torrents tr").expect("table#torrents tr");
        let magnet_sel = Selector::parse("a[href^='magnet:?']").expect("magnet a");
        let poster_sel = Selector::parse("img.posterIcon").expect("img.posterIcon");
        let category_sel = Selector::parse("div.fcat").expect("div.fcat");

        for target in targets {
            if ctx.cancel.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            rate_limit::wait(&domain, 3).await;
            let html = match fetch_plain(client, &target.url).await {
                Some(fr) => fr.html,
                None => {
                    warn!("arab_torrents: fetch failed for {}", target.url);
                    continue;
                }
            };

            let parsed_rows: Vec<ParsedRow> = {
                let doc = Html::parse_document(&html);
                let mut rows: Vec<_> = doc.select(&row_sel).collect();
                rows.reverse();
                let mut out = Vec::new();
                for row in rows {
                    let magnet_link = row
                        .select(&magnet_sel)
                        .next()
                        .and_then(|a| a.value().attr("href"))
                        .map(str::to_string);
                    let Some(magnet_link) = magnet_link else {
                        continue;
                    };

                    let mut title = row
                        .select(&magnet_sel)
                        .next()
                        .map(|a| a.text().collect::<String>())
                        .unwrap_or_default();
                    title = title.replace("تحميل", "").trim().to_string();
                    if title.is_empty() || parser::contains_adult_keywords(&title) {
                        continue;
                    }

                    let (language, video_type) = if search_keyword.is_some() {
                        let category = row
                            .select(&category_sel)
                            .next()
                            .map(|el| el.text().collect::<String>())
                            .unwrap_or_default();
                        let vt = if category.contains("مسلسلات") {
                            "series"
                        } else {
                            "movie"
                        };
                        ("Arabic".to_string(), vt.to_string())
                    } else {
                        (target.language.clone(), target.video_type.clone())
                    };

                    let poster_href = row
                        .select(&poster_sel)
                        .next()
                        .and_then(|img| {
                            img.value().attr("src").map(str::to_string).or_else(|| {
                                img.parent()
                                    .and_then(|p| p.value().as_element())
                                    .and_then(|el| el.attr("href"))
                                    .map(str::to_string)
                            })
                        })
                        .filter(|p| !p.is_empty());

                    out.push(ParsedRow {
                        magnet_link,
                        title,
                        language,
                        video_type,
                        poster_href,
                    });
                }
                out
            };

            for item in parsed_rows {
                if ctx.cancel.is_cancelled() {
                    return Err(JobError::Cancelled);
                }

                let info_hash = parser::extract_info_hash(&item.magnet_link)
                    .map(|h| h.to_lowercase())
                    .filter(|h| h.len() == 40);
                let Some(info_hash) = info_hash else {
                    debug!("arab_torrents: no info_hash in magnet for '{}'", item.title);
                    continue;
                };

                let catalog_id =
                    format!("{}_{}", item.language.to_ascii_lowercase(), item.video_type);
                let is_series = item.video_type == "series";
                let media_type = if is_series { "series" } else { "movie" };

                let parsed = parser::parse_title(&item.title);
                let files = if is_series {
                    build_series_files(&parsed, None, None)
                } else {
                    vec![]
                };

                let stream = ScrapedStream {
                    info_hash,
                    name: item.title.clone(),
                    source: SOURCE.to_string(),
                    seeders: None,
                    size: None,
                    parsed,
                    files,
                    is_cached: false,
                    torrent_type: crate::db::TorrentType::Public,
                    torrent_file: None,
                    announce_list: crate::scrapers::torrent_metadata::announce_list_from_magnet(
                        &item.magnet_link,
                    ),
                };

                let cfg = &ctx.state.config;
                if let Some(meta) = media_resolve::search_meta_for_scraped(
                    pool,
                    client,
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
                    media_resolve::link_to_catalogs(pool, meta.media_id.0, &[&catalog_id]).await;

                    if let Some(poster_href) = item.poster_href {
                        let poster_url = if poster_href.starts_with("http") {
                            poster_href
                        } else {
                            format!("{homepage}{poster_href}")
                        };
                        crate::db::upsert_primary_image(
                            pool,
                            meta.media_id.0,
                            "poster",
                            &poster_url,
                        )
                        .await;
                    }
                }
            }
        }

        Ok(())
    }
}

fn load_spider_config(config_path: &str) -> (String, serde_json::Value) {
    let default_catalogs = serde_json::json!({});
    if let Ok(text) = std::fs::read_to_string(config_path) {
        if let Ok(root) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(node) = root.get("arab_torrents") {
                let homepage = node
                    .get("homepage")
                    .and_then(|v| v.as_str())
                    .unwrap_or(DEFAULT_HOMEPAGE)
                    .trim_end_matches('/')
                    .to_string();
                let catalogs = node.get("catalogs").cloned().unwrap_or(default_catalogs);
                return (homepage, catalogs);
            }
        }
    }
    (DEFAULT_HOMEPAGE.to_string(), default_catalogs)
}

fn build_targets(
    homepage: &str,
    catalogs: &serde_json::Value,
    pages: u32,
    start_page: u32,
    search_keyword: Option<&str>,
    scrap_catalog_id: &str,
) -> Vec<ForumTarget> {
    if let Some(keyword) = search_keyword {
        return vec![ForumTarget {
            url: format!("{homepage}/index.php?search={keyword}"),
            language: "Arabic".to_string(),
            video_type: "movie".to_string(),
        }];
    }

    if scrap_catalog_id != "all" {
        let Some((language, video_type)) = scrap_catalog_id.split_once('_') else {
            warn!("arab_torrents: invalid scrap_catalog_id '{scrap_catalog_id}'");
            return vec![];
        };
        let forum_ids = catalogs.get(language).and_then(|lang| lang.get(video_type));
        return paginate_forum_ids(homepage, language, video_type, forum_ids, pages, start_page);
    }

    let mut targets = Vec::new();
    if let Some(catalog_map) = catalogs.as_object() {
        for (language, lang_val) in catalog_map {
            if let Some(types) = lang_val.as_object() {
                for (video_type, forum_ids) in types {
                    targets.extend(paginate_forum_ids(
                        homepage,
                        language,
                        video_type,
                        Some(forum_ids),
                        pages,
                        start_page,
                    ));
                }
            }
        }
    }
    targets
}

fn paginate_forum_ids(
    homepage: &str,
    language: &str,
    video_type: &str,
    forum_ids: Option<&serde_json::Value>,
    pages: u32,
    start_page: u32,
) -> Vec<ForumTarget> {
    let mut ids: Vec<String> = Vec::new();
    match forum_ids {
        Some(serde_json::Value::String(s)) => ids.push(s.clone()),
        Some(serde_json::Value::Array(arr)) => {
            for v in arr {
                if let Some(s) = v.as_str() {
                    ids.push(s.to_string());
                }
            }
        }
        _ => {}
    }
    if ids.is_empty() {
        return vec![];
    }

    let language_label = language
        .chars()
        .next()
        .map(|c| c.to_uppercase().collect::<String>() + &language[1..])
        .unwrap_or_else(|| language.to_string());

    let mut targets = Vec::new();
    for forum_id in ids {
        for page in start_page..start_page + pages {
            targets.push(ForumTarget {
                url: format!("{homepage}/index.php?cat={forum_id}&p={page}"),
                language: language_label.clone(),
                video_type: video_type.to_string(),
            });
        }
    }
    targets
}
