/// Public torrent indexer scraper (stub).
///
/// The Python implementation uses Scrapling with Cloudflare challenge solving to
/// scrape HTML from sites like BT4G.  Porting a headless-browser / challenge-solver
/// stack to Rust is out of scope for PR 4.  Stub returns empty — Python's background
/// scraper populates the DB from these sources independently.
use crate::scrapers::{ScrapedStream, SearchMeta};

#[allow(dead_code)]
pub async fn scrape(
    _meta: &SearchMeta,
    _media_type: &str,
    _season: Option<i32>,
    _episode: Option<i32>,
) -> Vec<ScrapedStream> {
    vec![]
}
