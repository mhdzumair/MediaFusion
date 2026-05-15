use async_trait::async_trait;
use scraper::Html;
use tracing::{debug, info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{
        fetcher, persist,
        public_indexer_registry::ALL_INDEXERS,
        public_indexers::{extract_row_data_pub, parse_size_bytes_pub},
        ScrapedStream, SearchMeta,
    },
    util::rate_limit,
};

pub struct RegistryCrawl;

#[async_trait]
impl JobHandler for RegistryCrawl {
    const QUEUE: &'static str = "spider_registry_crawl";
    const CONCURRENCY: usize = 2;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let indexer_name = args
            .get("indexer")
            .and_then(|v| v.as_str())
            .unwrap_or_default();

        if indexer_name.is_empty() {
            warn!("registry_crawl: no indexer specified in args");
            return Ok(());
        }

        let indexer = match ALL_INDEXERS.iter().find(|d| d.key == indexer_name) {
            Some(d) => d,
            None => {
                warn!("registry_crawl: unknown indexer '{indexer_name}'");
                return Ok(());
            }
        };

        let crawl = match &indexer.crawl {
            Some(c) => c,
            None => {
                warn!("registry_crawl: indexer '{indexer_name}' has no crawl config, skipping");
                return Ok(());
            }
        };

        let client = &ctx.state.http;
        let pool = &ctx.state.pool;
        let byparr_url = ctx.state.config.byparr_url.as_deref();

        let mut movie_streams: Vec<ScrapedStream> = Vec::new();
        let mut series_streams: Vec<ScrapedStream> = Vec::new();
        let mut seen = std::collections::HashSet::new();

        let domain = rate_limit::domain_key(crawl.browse_url);

        for page in 1..=crawl.max_pages {
            if ctx.cancel.is_cancelled() {
                debug!("registry_crawl: cancelled before page {page}");
                return Err(JobError::Cancelled);
            }

            let url = crawl.browse_url.replace("{page}", &page.to_string());

            // Rate limit per page
            rate_limit::wait(&domain, 2).await;

            let fr = match fetcher::fetch_for_indexer(
                client,
                byparr_url,
                &url,
                indexer.solve_cloudflare,
                indexer.http_fallback,
            )
            .await
            {
                Some(r) => r,
                None => {
                    warn!("registry_crawl: fetch failed for {url}");
                    continue;
                }
            };

            let base_url = fr.final_url.clone();

            // Extract row data synchronously so the non-Send Html is dropped before any await
            let row_data = {
                let doc = Html::parse_document(&fr.html);
                let data = extract_row_data_pub(&doc, indexer);
                if data.is_empty() {
                    debug!("registry_crawl: no rows on page {page} of {indexer_name}");
                }
                data
            };

            for row in row_data {
                if ctx.cancel.is_cancelled() {
                    debug!("registry_crawl: cancelled during row processing");
                    return Err(JobError::Cancelled);
                }

                let title = row.title.trim().to_string();
                if title.is_empty() {
                    continue;
                }

                if parser::contains_adult_keywords(&title) {
                    continue;
                }

                // Try to get info hash from direct magnet
                let direct_hash = row
                    .magnet_href
                    .as_deref()
                    .filter(|m| m.starts_with("magnet:"))
                    .and_then(parser::extract_info_hash)
                    .map(|h| h.to_lowercase());

                let info_hash = match direct_hash {
                    Some(h) => h,
                    None => {
                        // Try detail page if we have a href
                        let detail_href = match row.detail_href {
                            Some(ref d) => d.clone(),
                            None => continue,
                        };
                        if detail_href.len() > indexer.max_detail_url_length {
                            continue;
                        }
                        let detail_url = match resolve_url(&base_url, &detail_href) {
                            Some(u) => u,
                            None => continue,
                        };
                        rate_limit::wait(&domain, 2).await;
                        let dr = match fetcher::fetch_for_indexer(
                            client,
                            byparr_url,
                            &detail_url,
                            indexer.solve_cloudflare,
                            indexer.http_fallback,
                        )
                        .await
                        {
                            Some(r) => r,
                            None => continue,
                        };
                        match find_magnet_in_html(&dr.html)
                            .as_deref()
                            .and_then(parser::extract_info_hash)
                        {
                            Some(h) => h.to_lowercase(),
                            None => continue,
                        }
                    }
                };

                if !seen.insert(info_hash.clone()) {
                    continue;
                }

                let size = row.size_str.as_deref().and_then(parse_size_bytes_pub);
                let seeders = row
                    .seeder_str
                    .as_deref()
                    .and_then(|s| s.trim().parse::<i32>().ok());

                let parsed = parser::parse_title(&title);
                let media_type = if parsed.seasons.is_empty() && parsed.episodes.is_empty() {
                    "movie"
                } else {
                    "series"
                };

                let stream = ScrapedStream {
                    info_hash,
                    name: title,
                    source: indexer.source_name.to_string(),
                    seeders,
                    size,
                    parsed,
                    files: vec![],
                    is_cached: false,
                };

                if media_type == "movie" {
                    movie_streams.push(stream);
                } else {
                    series_streams.push(stream);
                }
            }

            info!(
                "registry_crawl: page {}/{} done for {indexer_name}, running total {} items",
                page,
                crawl.max_pages,
                movie_streams.len() + series_streams.len()
            );
        }

        info!(
            "registry_crawl: {indexer_name} done — {} movies, {} series",
            movie_streams.len(),
            series_streams.len()
        );

        if !movie_streams.is_empty() {
            let meta = SearchMeta {
                media_id: 0,
                imdb_id: None,
                title: String::new(),
                year: None,
            };
            persist::write_back(&movie_streams, pool, &meta, "movie", None, None).await;
        }

        if !series_streams.is_empty() {
            let meta = SearchMeta {
                media_id: 0,
                imdb_id: None,
                title: String::new(),
                year: None,
            };
            persist::write_back(&series_streams, pool, &meta, "series", None, None).await;
        }

        Ok(())
    }
}

// ─── Helpers (mirrors public_indexers.rs private fns) ────────────────────────

fn find_magnet_in_html(html: &str) -> Option<String> {
    use std::sync::OnceLock;
    static MAGNET_RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = MAGNET_RE.get_or_init(|| {
        regex::Regex::new(r#"magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^"'<>\s]*"#).unwrap()
    });
    re.find(html).map(|m| m.as_str().to_string())
}

fn resolve_url(base: &str, href: &str) -> Option<String> {
    if href.is_empty() {
        return None;
    }
    if href.starts_with("magnet:") || href.starts_with("http://") || href.starts_with("https://") {
        return Some(href.to_string());
    }
    if href.starts_with("//") {
        let scheme = if base.starts_with("https") {
            "https"
        } else {
            "http"
        };
        return Some(format!("{scheme}:{href}"));
    }
    let origin = base
        .find("://")
        .and_then(|i| base[i + 3..].find('/').map(|j| &base[..i + 3 + j]))
        .unwrap_or_else(|| base.trim_end_matches('/'));
    if href.starts_with('/') {
        Some(format!("{origin}{href}"))
    } else {
        let base_dir = base.rfind('/').map(|i| &base[..i]).unwrap_or(base);
        Some(format!("{base_dir}/{href}"))
    }
}
