use crate::models::user_data::UserData;
use crate::scrapers::ScrapedStream;

/// Apply user nudity and certification filters to a stream list.
pub fn apply_filters(streams: Vec<ScrapedStream>, _user_data: &UserData) -> Vec<ScrapedStream> {
    // Quality and content filters are applied at the DB level via nudity_status.
    // At the scraper level we only drop blatant adult content detected from the title.
    streams
        .into_iter()
        .filter(|s| !crate::parser::contains_adult_keywords(&s.name))
        .collect()
}
