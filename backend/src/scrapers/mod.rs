pub mod anilist;
pub mod browser;
pub mod easynews;
pub mod fetcher;
pub mod indexer_credentials;
pub mod jackett;
pub mod kitsu;
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
pub mod torrent_metadata;
pub mod torrentio;
pub mod torznab;
pub mod tvdb;
pub mod zilean;

use crate::db::TorrentType;
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
    pub torrent_type: TorrentType,
    /// Raw `.torrent` bytes — only populated for private/semi-private indexers.
    pub torrent_file: Option<Vec<u8>>,
    pub announce_list: Vec<String>,
}

impl ScrapedStream {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        info_hash: String,
        name: String,
        source: String,
        seeders: Option<i32>,
        size: Option<i64>,
        parsed: ParsedTitle,
        files: Vec<StreamFile>,
        is_cached: bool,
    ) -> Self {
        Self {
            info_hash,
            name,
            source,
            seeders,
            size,
            parsed,
            files,
            is_cached,
            torrent_type: TorrentType::Public,
            torrent_file: None,
            announce_list: Vec::new(),
        }
    }
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
    pub media_id: crate::db::MediaId,
    pub imdb_id: Option<String>,
    pub title: String,
    pub year: Option<i32>,
}
