/// Scraper for 5movierulz.day — Indian cinema magnet listings (WordPress catalog site).
///
/// Site structure:
///   - Homepage widget lists recent movie pages
///   - Language hub pages (e.g. `tamil-movie-free`, `bollywood-movie-free`) list
///     movie permalinks with `/page/N/` pagination
///   - Movie pages embed multiple `magnet:` links and an `h2.entry-title`
use async_trait::async_trait;
use regex::Regex;
use scraper::{Html, Selector};
use std::collections::HashSet;
use tracing::{debug, info};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{
        ScrapedStream, SearchMeta,
        fetcher::{fetch_byparr, fetch_plain},
        media_resolve, stream_convert,
    },
    util::{rate_limit, retry},
};

use super::spider_args::parse_listing_page_args;

const SOURCE: &str = "MovieRulz";
const CATALOG: &str = "movierulz";

fn load_movierulz_config(config_path: &str) -> (String, Vec<String>) {
    let default_homepage = "https://www.5movierulz.day".to_string();
    let default_categories = default_category_urls(&default_homepage);

    if let Ok(text) = std::fs::read_to_string(config_path)
        && let Ok(root) = serde_json::from_str::<serde_json::Value>(&text)
        && let Some(cfg) = root.get("movierulz")
    {
        let homepage = cfg
            .get("homepage")
            .and_then(|v| v.as_str())
            .unwrap_or(&default_homepage)
            .trim_end_matches('/')
            .to_string();
        let categories = cfg
            .get("categories")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect::<Vec<_>>()
            })
            .filter(|v| !v.is_empty())
            .unwrap_or_else(|| default_category_urls(&homepage));
        return (homepage, categories);
    }

    (default_homepage, default_categories)
}

fn default_category_urls(homepage: &str) -> Vec<String> {
    let base = homepage.trim_end_matches('/');
    [
        format!("{base}/"),
        format!("{base}/bollywood-movie-free/"),
        format!("{base}/telugu-movie/"),
        format!("{base}/tamil-movie-free/"),
        format!("{base}/malayalam-movie-online/"),
    ]
    .to_vec()
}

fn is_homepage_seed(seed: &str, homepage: &str) -> bool {
    seed.trim_end_matches('/') == homepage.trim_end_matches('/')
}

fn resolve_url(base: &str, href: &str) -> String {
    if href.starts_with("http") {
        href.to_string()
    } else {
        format!("{}{}", base.trim_end_matches('/'), href)
    }
}

fn is_movie_page_url(url: &str) -> bool {
    url.contains("watch-online-free")
        || url.contains("watch-online")
        || (url.contains("-movie-") && url.ends_with('/'))
}

fn extract_movie_links(html: &str, base_url: &str) -> Vec<String> {
    static HREF_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = HREF_RE.get_or_init(|| Regex::new(r#"href="([^"]+)""#).expect("href regex"));

    re.captures_iter(html)
        .filter_map(|caps| caps.get(1).map(|m| m.as_str()))
        .map(|href| resolve_url(base_url, href))
        .filter(|url| is_movie_page_url(url))
        .collect()
}

fn parse_movie_page(html: &str) -> (String, Vec<String>) {
    let doc = Html::parse_document(html);
    let title_sel = Selector::parse("h2.entry-title").unwrap();
    let title = doc
        .select(&title_sel)
        .next()
        .map(|el| el.text().collect::<String>().trim().to_string())
        .unwrap_or_default();

    static MAGNET_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let magnet_re = MAGNET_RE
        .get_or_init(|| Regex::new(r"magnet:\?xt=urn:btih:[^[:space:]<>]+").expect("magnet regex"));

    let magnets: Vec<String> = magnet_re
        .find_iter(html)
        .map(|m| m.as_str().replace("&amp;", "&"))
        .collect();

    (title, magnets)
}

fn parse_media_title(raw: &str) -> (String, Option<i32>) {
    let parsed = parser::parse_title(raw);
    let title = parsed
        .title
        .clone()
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| raw.to_string());
    (title, parsed.year)
}

async fn fetch_html(
    label: &str,
    url: &str,
    client: &reqwest::Client,
    byparr_url: &Option<String>,
) -> Option<String> {
    retry::with_retry(label, || {
        let url = url.to_string();
        let client = client.clone();
        let bp = byparr_url.clone();
        async move {
            if let Some(bp_url) = &bp
                && let Some(r) = fetch_byparr(&client, bp_url, &url).await
            {
                return Ok(r.html);
            }
            fetch_plain(&client, &url)
                .await
                .map(|r| r.html)
                .ok_or_else(|| format!("fetch failed: {url}"))
        }
    })
    .await
    .ok()
}

async fn scrape_movierulz(args: &serde_json::Value, ctx: &JobCtx) -> Result<(), JobError> {
    let (pages, start_page) = parse_listing_page_args(args);
    let (homepage, seed_urls) = load_movierulz_config(&ctx.state.config.scraper_config_path);
    let client = &ctx.state.http;
    let pool = &ctx.state.pool;
    let byparr_url = ctx.state.config.byparr_url.clone();
    let tmdb_api_key = ctx.state.config.tmdb_api_key.as_deref();
    let cinemeta_fallback = ctx.state.config.imdb_cinemeta_fallback_enabled;

    let rate_key = rate_limit::domain_key(&homepage);
    let mut movie_urls: HashSet<String> = HashSet::new();

    for seed in seed_urls {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        if is_homepage_seed(&seed, &homepage) {
            rate_limit::wait(&rate_key, 1).await;
            if let Some(html) = fetch_html(SOURCE, &seed, client, &byparr_url).await {
                for url in extract_movie_links(&html, &homepage) {
                    movie_urls.insert(url);
                }
            }
            continue;
        }

        for page in start_page..start_page.saturating_add(pages) {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let listing_url = if page == 1 {
                seed.clone()
            } else {
                format!("{}/page/{page}/", seed.trim_end_matches('/'))
            };

            rate_limit::wait(&rate_key, 1).await;
            let Some(html) = fetch_html(SOURCE, &listing_url, client, &byparr_url).await else {
                break;
            };

            let links = extract_movie_links(&html, &homepage);
            if links.is_empty() {
                break;
            }
            for url in links {
                movie_urls.insert(url);
            }
        }
    }

    info!("{SOURCE}: discovered {} movie pages", movie_urls.len());

    let mut total_streams = 0usize;
    for movie_url in movie_urls {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        rate_limit::wait(&rate_key, 1).await;
        let Some(html) = fetch_html(SOURCE, &movie_url, client, &byparr_url).await else {
            continue;
        };

        let (page_title, magnets) = parse_movie_page(&html);
        if page_title.is_empty() || magnets.is_empty() {
            continue;
        }

        let (media_title, year) = parse_media_title(&page_title);
        let media = media_resolve::find_or_create_media(
            pool,
            client,
            &media_title,
            year,
            false,
            &[CATALOG],
            tmdb_api_key,
            cinemeta_fallback,
        )
        .await;

        let Some(media) = media else {
            debug!("{SOURCE}: could not resolve media for {page_title}");
            continue;
        };

        media_resolve::link_to_catalogs(pool, media.id, &[CATALOG]).await;

        let mut block_streams = Vec::new();
        for magnet in magnets {
            let Some(info_hash) = parser::extract_info_hash(&magnet).map(|h| h.to_lowercase())
            else {
                continue;
            };
            let parsed = parser::parse_title(&page_title);
            block_streams.push(ScrapedStream {
                info_hash,
                name: page_title.clone(),
                source: SOURCE.to_string(),
                seeders: None,
                size: None,
                parsed,
                files: vec![],
                is_cached: false,
                torrent_type: crate::db::TorrentType::Public,
                torrent_file: None,
                announce_list: vec![],
                uploader: None,
            });
        }

        if block_streams.is_empty() {
            continue;
        }

        let meta = SearchMeta {
            media_id: crate::db::MediaId(media.id),
            imdb_id: None,
            title: media.title.clone(),
            year: media.year,
        };
        stream_convert::write_back_torrents(pool, &block_streams, &meta, "movie", None, None).await;
        total_streams += block_streams.len();
    }

    info!("{SOURCE}: wrote {total_streams} streams");
    Ok(())
}

pub struct MovieRulzCrawl;

#[async_trait]
impl JobHandler for MovieRulzCrawl {
    const QUEUE: &'static str = "spider_movierulz";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_movierulz(&args, &ctx).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn homepage_seed_detection() {
        assert!(is_homepage_seed(
            "https://www.5movierulz.day/",
            "https://www.5movierulz.day"
        ));
        assert!(!is_homepage_seed(
            "https://www.5movierulz.day/tamil-movie-free/",
            "https://www.5movierulz.day"
        ));
    }

    #[test]
    fn default_categories_use_language_hubs() {
        let urls = default_category_urls("https://www.5movierulz.day");
        assert!(urls.iter().any(|u| u.contains("tamil-movie-free")));
        assert!(urls.iter().any(|u| u.contains("bollywood-movie-free")));
        assert!(!urls.iter().any(|u| u.contains("/category/")));
    }

    #[test]
    fn movie_page_url_detection() {
        assert!(is_movie_page_url(
            "https://www.5movierulz.day/peter-2026-hdrip-original-tamil-malayalam-movie-watch-online-free/"
        ));
        assert!(!is_movie_page_url(
            "https://www.5movierulz.day/category/telugu-movies-2025/"
        ));
    }

    #[test]
    fn extracts_magnets_from_sample_html() {
        let html = r#"<h2 class="entry-title">Peter (2026) HDRip</h2>
            <a href="magnet:?xt=urn:btih:abc123&dn=test">Download</a>"#;
        let (title, magnets) = parse_movie_page(html);
        assert_eq!(title, "Peter (2026) HDRip");
        assert_eq!(magnets.len(), 1);
        assert!(magnets[0].starts_with("magnet:"));
    }
}
