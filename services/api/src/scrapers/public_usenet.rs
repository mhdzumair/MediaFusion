/// Public Usenet indexer scraper (stub).
///
/// The Python implementation scrapes Binsearch/NZBIndex HTML with a custom parser.
/// Stub returns empty — Python's background scraper populates these independently.
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
