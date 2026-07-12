use crate::db::{
    MediaType, StreamFileStoreInput, StreamStoreBase, TelegramStoreInput, TorrentStoreInput,
    UsenetStoreInput,
};
use crate::scrapers::{ScrapedStream, ScrapedTelegramStream, ScrapedUsenetStream};

impl From<&ScrapedStream> for TorrentStoreInput {
    fn from(s: &ScrapedStream) -> Self {
        let files = s
            .files
            .iter()
            .map(|f| StreamFileStoreInput {
                file_index: f.file_index,
                filename: f.filename.clone(),
                size: None,
                season_number: f.season_number,
                episode_number: f.episode_number,
            })
            .collect();

        let mut base = StreamStoreBase::from_parsed(s.name.clone(), s.source.clone(), &s.parsed)
            .scraper_defaults();
        base.uploader = s.uploader.clone();

        Self {
            base,
            info_hash: s.info_hash.clone(),
            total_size: s.size.unwrap_or(0),
            seeders: s.seeders,
            torrent_type: s.torrent_type,
            torrent_file: s.torrent_file.clone(),
            announce_list: s.announce_list.clone(),
            files,
        }
    }
}

impl From<&ScrapedUsenetStream> for UsenetStoreInput {
    fn from(s: &ScrapedUsenetStream) -> Self {
        let files = s
            .files
            .iter()
            .map(|f| StreamFileStoreInput {
                file_index: f.file_index,
                filename: f.filename.clone(),
                size: None,
                season_number: f.season_number,
                episode_number: f.episode_number,
            })
            .collect();

        Self {
            base: StreamStoreBase::from_parsed(s.name.clone(), s.source.clone(), &s.parsed)
                .scraper_defaults(),
            nzb_guid: s.nzb_guid.clone(),
            nzb_url: s.nzb_url.clone(),
            size: s.size,
            indexer: s.indexer.clone(),
            group_name: s.group_name.clone(),
            is_passworded: false,
            files,
        }
    }
}

impl From<&ScrapedTelegramStream> for TelegramStoreInput {
    fn from(s: &ScrapedTelegramStream) -> Self {
        Self {
            base: StreamStoreBase::from_parsed(s.name.clone(), "Telegram".to_string(), &s.parsed)
                .scraper_defaults(),
            chat_id: s.chat_id.to_string(),
            chat_username: s.chat_username.clone(),
            message_id: s.message_id,
            file_name: s.file_name.clone(),
            size: s.size,
            mime_type: s.mime_type.clone(),
            file_id: None,
            file_unique_id: None,
            backup_chat_id: None,
            backup_message_id: None,
        }
    }
}

/// Build store options for scraper write-back from wire media type string.
pub fn scraper_store_opts(
    media_id: crate::db::MediaId,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> crate::db::StoreStreamOpts {
    let mt = MediaType::from_wire(media_type).unwrap_or(MediaType::Movie);
    crate::db::StoreStreamOpts::scraper(media_id, mt).with_episode(season, episode, None)
}

/// Scraper/job cold-path torrent persistence (replaces `persist::write_back`).
pub async fn write_back_torrents(
    pool: &sqlx::PgPool,
    streams: &[ScrapedStream],
    meta: &crate::scrapers::SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) {
    if streams.is_empty() {
        return;
    }
    if meta.media_id.0 <= 0 {
        return;
    }
    let opts = scraper_store_opts(meta.media_id, media_type, season, episode);
    let normalized: Vec<TorrentStoreInput> = streams.iter().map(TorrentStoreInput::from).collect();
    crate::db::store_torrent_streams(pool, &normalized, &opts).await;
}

/// Scraper/job cold-path usenet persistence.
pub async fn write_back_usenet(
    pool: &sqlx::PgPool,
    streams: &[ScrapedUsenetStream],
    meta: &crate::scrapers::SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) {
    if streams.is_empty() {
        return;
    }
    let opts = scraper_store_opts(meta.media_id, media_type, season, episode);
    let normalized: Vec<UsenetStoreInput> = streams.iter().map(UsenetStoreInput::from).collect();
    crate::db::store_usenet_streams(pool, &normalized, &opts).await;
}

/// Scraper/job cold-path telegram persistence. Returns true when a new stream was inserted.
pub async fn write_back_telegram(
    pool: &sqlx::PgPool,
    streams: &[ScrapedTelegramStream],
    meta: &crate::scrapers::SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> bool {
    if streams.is_empty() {
        return false;
    }
    let opts = scraper_store_opts(meta.media_id, media_type, season, episode);
    let mut inserted = false;
    for stream in streams {
        let input = TelegramStoreInput::from(stream);
        match crate::db::store_telegram_stream(pool, &input, &opts).await {
            Ok(r) if r.was_inserted() => inserted = true,
            Ok(_) => {}
            Err(e) => tracing::warn!(
                "store_telegram_stream: failed chat={} msg={} — {e}",
                input.chat_id,
                input.message_id
            ),
        }
    }
    inserted
}
