pub mod browser;
pub mod easynews;
pub mod fetcher;
pub mod jackett;
pub mod media_resolve;
pub mod mediafusion;
pub mod metadata;
pub mod newznab;
pub mod orchestrator;
pub mod persist;
pub mod prowlarr;
pub mod public_indexer_registry;
pub mod public_indexers;
pub mod public_usenet;
pub mod rss;
pub mod source_health;
pub mod telegram;
pub mod torbox_search;
pub mod torrentio;
pub mod torznab;
pub mod zilean;

use crate::parser::ParsedTitle;

/// A usenet stream result (e.g. from Easynews).
#[derive(Debug, Clone)]
pub struct ScrapedUsenetStream {
    pub nzb_guid: String,
    pub nzb_url: String,
    pub name: String,
    pub size: i64,
    pub indexer: String,
    pub source: String,
    pub group_name: Option<String>,
    pub parsed: ParsedTitle,
    pub files: Vec<StreamFile>,
    pub is_cached: bool,
}

/// A torrent stream result from any scraper source.
#[derive(Debug, Clone)]
pub struct ScrapedStream {
    pub info_hash: String,
    pub name: String,
    pub source: String,
    pub seeders: Option<i32>,
    pub size: Option<i64>,
    pub parsed: ParsedTitle,
    /// For series: one entry per episode this torrent covers.
    pub files: Vec<StreamFile>,
    pub is_cached: bool,
}

#[derive(Debug, Clone)]
pub struct StreamFile {
    pub file_index: i32,
    pub filename: String,
    pub season_number: i32,
    pub episode_number: i32,
}

/// A Telegram document stream scraped at request time.
#[derive(Debug, Clone)]
pub struct ScrapedTelegramStream {
    pub chat_id: i64,
    pub chat_username: Option<String>,
    pub message_id: i32,
    pub file_name: String,
    pub size: i64,
    pub mime_type: Option<String>,
    /// Always "telegram".
    pub source: String,
    /// PTT-formatted display name (same as file_name for now).
    pub name: String,
    pub parsed: crate::parser::ParsedTitle,
    pub season: Option<i32>,
    pub episode: Option<i32>,
}

/// Metadata passed to scrapers at request time.
#[derive(Debug, Clone)]
pub struct SearchMeta {
    pub media_id: i64,
    pub imdb_id: Option<String>,
    pub title: String,
    pub year: Option<i32>,
}
