use serde_json::Value;

use super::types::{LinkSource, MediaId, MediaType, TorrentType};

/// Shared `stream` row fields (quality flags, uploader, visibility).
#[derive(Debug, Clone)]
pub struct StreamStoreBase {
    pub name: String,
    pub source: String,
    pub resolution: Option<String>,
    pub codec: Option<String>,
    pub quality: Option<String>,
    pub release_group: Option<String>,
    pub bit_depth: Option<String>,
    pub is_proper: bool,
    pub is_repack: bool,
    pub is_extended: bool,
    pub is_complete: bool,
    pub is_dubbed: bool,
    pub is_subbed: bool,
    pub is_remastered: bool,
    pub is_upscaled: bool,
    pub uploader: Option<String>,
    pub uploader_user_id: Option<i32>,
    pub is_public: bool,
    pub is_active: bool,
    pub is_blocked: bool,
}

impl Default for StreamStoreBase {
    fn default() -> Self {
        Self {
            name: String::new(),
            source: String::new(),
            resolution: None,
            codec: None,
            quality: None,
            release_group: None,
            bit_depth: None,
            is_proper: false,
            is_repack: false,
            is_extended: false,
            is_complete: false,
            is_dubbed: false,
            is_subbed: false,
            is_remastered: false,
            is_upscaled: false,
            uploader: None,
            uploader_user_id: None,
            is_public: true,
            is_active: true,
            is_blocked: false,
        }
    }
}

impl StreamStoreBase {
    /// Build from a parsed torrent/usenet title (scraper and import paths).
    pub fn from_parsed(name: String, source: String, parsed: &crate::parser::ParsedTitle) -> Self {
        Self {
            name,
            source,
            resolution: parsed.resolution.clone(),
            codec: parsed.codec.clone(),
            quality: parsed.quality.clone(),
            release_group: parsed.release_group.clone(),
            bit_depth: None,
            is_proper: parsed.is_proper,
            is_repack: parsed.is_repack,
            is_extended: parsed.is_extended,
            is_complete: parsed.is_complete,
            is_dubbed: parsed.is_dubbed,
            is_subbed: parsed.is_subbed,
            is_remastered: parsed.is_remastered,
            is_upscaled: parsed.is_upscaled,
            ..Self::default()
        }
    }

    /// Scraper cold-path defaults (public, active).
    pub fn scraper_defaults(mut self) -> Self {
        self.is_public = true;
        self.is_active = true;
        self.is_blocked = false;
        self
    }
}

#[derive(Debug, Clone)]
pub struct StreamFileStoreInput {
    pub file_index: i32,
    pub filename: String,
    pub size: Option<i64>,
    pub season_number: i32,
    pub episode_number: i32,
}

#[derive(Debug, Clone)]
pub struct TorrentStoreInput {
    pub base: StreamStoreBase,
    pub info_hash: String,
    pub total_size: i64,
    pub seeders: Option<i32>,
    pub torrent_type: TorrentType,
    pub torrent_file: Option<Vec<u8>>,
    pub announce_list: Vec<String>,
    pub files: Vec<StreamFileStoreInput>,
}

#[derive(Debug, Clone)]
pub struct UsenetStoreInput {
    pub base: StreamStoreBase,
    pub nzb_guid: String,
    pub nzb_url: String,
    pub size: i64,
    pub indexer: String,
    pub group_name: Option<String>,
    pub is_passworded: bool,
    pub files: Vec<StreamFileStoreInput>,
}

#[derive(Debug, Clone)]
pub struct TelegramStoreInput {
    pub base: StreamStoreBase,
    /// Numeric chat id (scrapers) or string id (bot contributions).
    pub chat_id: String,
    pub chat_username: Option<String>,
    pub message_id: i32,
    pub file_name: String,
    pub size: i64,
    pub mime_type: Option<String>,
    /// Bot contribution fields (optional).
    pub file_id: Option<String>,
    pub file_unique_id: Option<String>,
    pub backup_chat_id: Option<String>,
    pub backup_message_id: Option<i32>,
}

#[derive(Debug, Clone)]
pub struct HttpStoreInput {
    pub base: StreamStoreBase,
    pub url: String,
    pub format: Option<String>,
    pub behavior_hints: Option<Value>,
    pub drm_key_id: Option<String>,
    pub drm_key: Option<String>,
    pub extractor_name: Option<String>,
}

#[derive(Debug, Clone)]
pub struct YoutubeStoreInput {
    pub base: StreamStoreBase,
    pub video_id: String,
    pub channel_id: Option<String>,
    pub channel_name: Option<String>,
    pub duration_seconds: Option<i32>,
    pub is_live: bool,
    pub is_premiere: bool,
}

#[derive(Debug, Clone)]
pub struct AcestreamStoreInput {
    pub base: StreamStoreBase,
    pub content_id: String,
    pub info_hash: Option<String>,
}

/// Options for linking a stored stream to media (and per-file series links).
#[derive(Debug, Clone)]
pub struct StoreStreamOpts {
    pub media_id: MediaId,
    pub media_type: MediaType,
    pub season: Option<i32>,
    pub episode: Option<i32>,
    pub link_source: LinkSource,
    pub is_primary: bool,
    pub is_verified: bool,
}

/// Resolve season/episode for a series file link when import JSON omits them.
/// Matches user-import behavior: default season 1, episode `file_index + 1`.
pub fn resolve_series_episode_numbers(
    file_index: i32,
    season: Option<i32>,
    episode: Option<i32>,
) -> (i32, i32) {
    (season.unwrap_or(1), episode.unwrap_or(file_index + 1))
}

impl StoreStreamOpts {
    pub fn scraper(media_id: MediaId, media_type: MediaType) -> Self {
        Self {
            media_id,
            media_type,
            season: None,
            episode: None,
            link_source: LinkSource::PttParser,
            is_primary: true,
            is_verified: false,
        }
    }

    pub fn with_episode(mut self, season: Option<i32>, episode: Option<i32>) -> Self {
        self.season = season;
        self.episode = episode;
        self
    }

    pub fn user_import(media_id: MediaId, media_type: MediaType) -> Self {
        Self {
            media_id,
            media_type,
            season: None,
            episode: None,
            link_source: LinkSource::User,
            is_primary: true,
            is_verified: false,
        }
    }
}
