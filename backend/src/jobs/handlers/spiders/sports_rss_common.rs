use reqwest::Client;
use sqlx::PgPool;
use tracing::debug;

use crate::{
    parser,
    scrapers::{ScrapedStream, SearchMeta, media_resolve, stream_convert},
};

pub struct SportsRssPersistCtx<'a> {
    pub http: &'a Client,
    pub tmdb_api_key: Option<&'a str>,
    pub cinemeta_fallback: bool,
}

pub async fn classify_sports_rss_release(
    title: &str,
    info_hash: &str,
    source: &str,
    pool: &PgPool,
    proxy_url: Option<&str>,
) -> (
    String,
    Option<i32>,
    &'static str,
    Vec<crate::scrapers::StreamFile>,
    parser::ParsedTitle,
) {
    let parsed = parser::parse_sports_title(title);
    let racing_info = parser::parse_racing_title(title);
    let drive_to_survive = parser::classify_drive_to_survive(title);

    if let Some((series_title, season, episode)) = drive_to_survive {
        let episode_title = parsed
            .episode_title
            .clone()
            .unwrap_or_else(|| parser::clean_sports_title(title));
        let files = vec![crate::scrapers::StreamFile {
            file_index: 0,
            filename: episode_title,
            season_number: season,
            episode_number: episode,
        }];
        return (series_title, None, "series", files, parsed);
    }

    if let Some(fighting) = parser::classify_fighting_series_title(title) {
        let episode_title = parser::clean_sports_title(title);
        let files = vec![crate::scrapers::StreamFile {
            file_index: 0,
            filename: episode_title,
            season_number: fighting.season_number,
            episode_number: fighting.episode_number,
        }];
        return (fighting.series_title, None, "series", files, parsed);
    }

    if let Some(ref racing) = racing_info {
        let display_title = racing
            .session
            .clone()
            .unwrap_or_else(|| parser::clean_sports_title(title));
        let files = super::formula_racing::resolve_racing_files(
            source,
            info_hash,
            &[],
            None,
            &display_title,
            pool,
            proxy_url,
        )
        .await;
        return (
            racing.series_title.clone(),
            racing.year,
            "series",
            files,
            parsed,
        );
    }

    if parser::detect_sports_category(title) == Some("fighting") {
        let clean = parser::clean_fighting_event_title(title);
        return (clean, parsed.year, "movie", vec![], parsed);
    }

    let clean = parsed.title.clone().unwrap_or_else(|| title.to_string());
    (clean, parsed.year, "movie", vec![], parsed)
}

async fn resolve_sports_rss_media_id(
    pool: &PgPool,
    title: &str,
    clean_title: &str,
    year: Option<i32>,
    effective_media_type: &str,
    catalog: &str,
    ctx: Option<&SportsRssPersistCtx<'_>>,
) -> i32 {
    if catalog == "fighting" {
        if let Some(c) = ctx
            && let Some(id) = media_resolve::resolve_fighting_media(
                pool,
                c.http,
                clean_title,
                year,
                effective_media_type,
                catalog,
                c.tmdb_api_key,
                c.cinemeta_fallback,
            )
            .await
        {
            return id;
        }
        let brand_poster = crate::poster::sports::random_poster_for_fighting_title(title);
        return media_resolve::find_or_create_sports_stub(
            pool,
            clean_title,
            year,
            brand_poster.as_deref(),
            &effective_media_type.to_uppercase(),
            catalog,
        )
        .await
        .unwrap_or(0);
    }

    media_resolve::find_or_create_sports_stub(
        pool,
        clean_title,
        year,
        None,
        &effective_media_type.to_uppercase(),
        catalog,
    )
    .await
    .unwrap_or(0)
}

pub async fn persist_sports_rss_stream(
    pool: &PgPool,
    title: &str,
    info_hash: &str,
    clean_title: String,
    year: Option<i32>,
    effective_media_type: &str,
    parsed: parser::ParsedTitle,
    files: Vec<crate::scrapers::StreamFile>,
    source: String,
    seeders: Option<i32>,
    size: Option<i64>,
    uploader: Option<String>,
    catalog: &str,
    ctx: Option<&SportsRssPersistCtx<'_>>,
) {
    let media_id = resolve_sports_rss_media_id(
        pool,
        title,
        &clean_title,
        year,
        effective_media_type,
        catalog,
        ctx,
    )
    .await;

    if media_id > 0 {
        media_resolve::link_to_catalogs(pool, media_id, &[catalog]).await;
        for f in &files {
            let _ = crate::db::upsert_series_episode(
                pool,
                crate::db::MediaId(media_id),
                f.season_number,
                f.episode_number,
                &f.filename,
            )
            .await;
        }
    }

    let extra_files_to_persist = (files.len() > 1).then(|| files.clone());
    let stream = ScrapedStream {
        info_hash: info_hash.to_string(),
        name: title.to_string(),
        source,
        seeders,
        size,
        parsed,
        files,
        is_cached: false,
        torrent_type: crate::db::TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
        uploader,
    };

    let meta = SearchMeta {
        media_id: crate::db::MediaId(media_id),
        imdb_id: None,
        title: clean_title,
        year,
    };
    stream_convert::write_back_torrents(pool, &[stream], &meta, effective_media_type, None, None)
        .await;

    if let Some(extra_files) = extra_files_to_persist {
        let entries: Vec<crate::db::streams::TorrentFileEntry> = extra_files
            .iter()
            .map(|f| crate::db::streams::TorrentFileEntry {
                file_index: f.file_index,
                filename: f.filename.clone(),
                size: 0,
                season: Some(f.season_number),
                episode: Some(f.episode_number),
            })
            .collect();
        let _ = crate::db::streams::upsert_stream_files(pool, info_hash, &entries).await;
    }

    debug!("sports_rss: persisted {info_hash} for '{title}' → catalog={catalog}");
}
