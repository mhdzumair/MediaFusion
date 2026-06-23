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
        ScrapedStream, fetcher, media_resolve,
        prowlarr::build_series_files,
        public_indexer_registry::ALL_INDEXERS,
        public_indexers::{extract_row_data_pub, parse_size_bytes_pub},
        stream_convert,
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
        let kf = ctx
            .state
            .keyword_filters
            .read()
            .map(|g| g.clone())
            .unwrap_or_default();

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

                if kf.matches_blocked_keyword(&title) {
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
                    source: indexer.source_name.to_string(),
                    seeders,
                    size,
                    parsed,
                    files,
                    is_cached: false,
                    torrent_type: crate::db::TorrentType::Public,
                    torrent_file: None,
                    announce_list: vec![],
                };

                let is_series = media_type == "series";
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

            info!(
                "registry_crawl: page {}/{} done for {indexer_name}",
                page, crawl.max_pages,
            );
        }

        info!("registry_crawl: {indexer_name} crawl complete");

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
