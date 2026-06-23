/// Newznab NZB indexer scraper.
///
/// Newznab uses the same Torznab-compatible XML feed format for search results,
/// so we reuse the XML parser from `torznab`.  The only differences are:
///   - The base endpoint is `{url}/api` (not the bare URL)
///   - Default categories are in the 2000/5000 ranges (same as torrent)
///   - `apikey` param instead of Prowlarr's `X-Api-Key` header
///   - Results carry NZB download URLs rather than magnet links; we can still
///     extract `<newznab:attr name="infohash">` when present.
use reqwest::Client;

use crate::{
    models::user_data::NewznabIndexer,
    scrapers::{ScrapedStream, SearchMeta, torznab},
    state::KeywordFilterCache,
};

const MOVIE_CATS: &[i64] = &[2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070];
const TV_CATS: &[i64] = &[5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070];

pub async fn scrape(
    client: &Client,
    indexers: &[NewznabIndexer],
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedStream> {
    use crate::models::user_data::TorznabEndpoint;

    // Convert each NewznabIndexer into a TorznabEndpoint by appending /api to the
    // URL and building default category lists, then delegate to the torznab scraper.
    let endpoints: Vec<TorznabEndpoint> = indexers
        .iter()
        .filter(|idx| idx.enabled)
        .map(|idx| {
            let api_url = format!("{}/api", idx.url.trim_end_matches('/'));
            let cats = if media_type == "series" {
                if idx.tv_categories.is_empty() {
                    TV_CATS.to_vec()
                } else {
                    idx.tv_categories.clone()
                }
            } else if idx.movie_categories.is_empty() {
                MOVIE_CATS.to_vec()
            } else {
                idx.movie_categories.clone()
            };

            let mut url_with_key = api_url;
            if let Some(ak) = &idx.api_key {
                if !ak.is_empty() {
                    url_with_key = format!("{url_with_key}?apikey={ak}");
                }
            }

            TorznabEndpoint {
                id: idx.id.clone(),
                name: idx.name.clone(),
                url: url_with_key,
                headers: None,
                enabled: true,
                categories: cats,
                priority: idx.priority,
            }
        })
        .collect();

    torznab::scrape(
        client,
        &endpoints,
        meta,
        media_type,
        season,
        episode,
        keyword_filters,
    )
    .await
}
