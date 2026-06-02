//! Integration tests for [`mediafusion_api::db::stream_store`] — all stream types and
//! series episode linking (file_media_link vs stream_media_link).

mod common;

use mediafusion_api::db::{
    link_file_to_media_episode, resolve_series_episode_numbers, store_acestream_stream,
    store_http_stream, store_telegram_stream, store_torrent_stream, store_usenet_stream,
    store_youtube_stream,
    types::{LinkSource, MediaId, MediaType},
    upsert_stream_file_row, upsert_torrent_files_by_hash, AcestreamStoreInput, HttpStoreInput,
    StoreStreamOpts, StoreStreamResult, StreamFileStoreInput, StreamStoreBase, TelegramStoreInput,
    TorrentFileEntry, TorrentStoreInput, TorrentType, UsenetStoreInput, YoutubeStoreInput,
};

struct Cleanup {
    pool: &'static sqlx::PgPool,
    media_ids: Vec<i32>,
    stream_ids: Vec<i32>,
}

impl Cleanup {
    fn new(pool: &'static sqlx::PgPool) -> Self {
        Self {
            pool,
            media_ids: vec![],
            stream_ids: vec![],
        }
    }

    async fn finish(self) {
        if !self.stream_ids.is_empty() {
            let _ = sqlx::query("DELETE FROM stream WHERE id = ANY($1)")
                .bind(&self.stream_ids)
                .execute(self.pool)
                .await;
        }
        if !self.media_ids.is_empty() {
            let _ = sqlx::query("DELETE FROM media WHERE id = ANY($1)")
                .bind(&self.media_ids)
                .execute(self.pool)
                .await;
        }
    }
}

async fn insert_media(pool: &sqlx::PgPool, media_type: MediaType, title: &str) -> i32 {
    sqlx::query_scalar(
        r#"INSERT INTO media (type, title, adult, is_blocked, is_public, is_user_created,
                              total_streams, nudity_status, created_at)
           VALUES ($1, $2, false, false, true, false, 0, 'UNKNOWN', NOW())
           RETURNING id"#,
    )
    .bind(media_type)
    .bind(title)
    .fetch_one(pool)
    .await
    .expect("insert media")
}

async fn stream_media_link_count(pool: &sqlx::PgPool, stream_id: i32, media_id: i32) -> i64 {
    sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_media_link WHERE stream_id = $1 AND media_id = $2",
    )
    .bind(stream_id)
    .bind(media_id)
    .fetch_one(pool)
    .await
    .unwrap_or(0)
}

async fn file_episode_link_exists(
    pool: &sqlx::PgPool,
    stream_id: i32,
    media_id: i32,
    season: i32,
    episode: i32,
) -> bool {
    let count: i64 = sqlx::query_scalar(
        r#"SELECT COUNT(*) FROM file_media_link fml
           JOIN stream_file sf ON sf.id = fml.file_id
           WHERE sf.stream_id = $1 AND fml.media_id = $2
             AND fml.season_number = $3 AND fml.episode_number = $4"#,
    )
    .bind(stream_id)
    .bind(media_id)
    .bind(season)
    .bind(episode)
    .fetch_one(pool)
    .await
    .unwrap_or(0);
    count > 0
}

fn sample_torrent(info_hash: &str) -> TorrentStoreInput {
    let parsed = mediafusion_api::parser::parse_title("Show.S01E02.1080p.mkv");
    TorrentStoreInput {
        base: StreamStoreBase::from_parsed(
            "Show.S01E02.1080p.mkv".to_string(),
            "test".to_string(),
            &parsed,
        ),
        info_hash: info_hash.to_string(),
        total_size: 2048,
        seeders: Some(5),
        torrent_type: TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
        files: vec![],
    }
}

// ─── Unit-style helpers ───────────────────────────────────────────────────────

#[test]
fn resolve_series_episode_numbers_defaults() {
    assert_eq!(resolve_series_episode_numbers(0, None, None), (1, 1));
    assert_eq!(resolve_series_episode_numbers(2, None, None), (1, 3));
    assert_eq!(resolve_series_episode_numbers(0, Some(2), Some(5)), (2, 5));
}

// ─── Torrent ───────────────────────────────────────────────────────────────────

#[tokio::test]
async fn store_torrent_movie_uses_stream_media_link() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Movie, "stream_store::torrent_movie").await;
    cleanup.media_ids.push(media_id);

    let info_hash = format!("mov{media_id:0>36}");
    let mut stream = sample_torrent(&info_hash);
    stream.files.clear();

    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Movie);
    let result = store_torrent_stream(pool, &stream, &opts)
        .await
        .expect("store");
    cleanup.stream_ids.push(result.stream_id().0);

    assert!(result.was_inserted());
    assert_eq!(
        stream_media_link_count(pool, result.stream_id().0, media_id).await,
        1
    );
    assert!(
        !file_episode_link_exists(pool, result.stream_id().0, media_id, 1, 1).await,
        "movies must not create file_media_link rows"
    );
    cleanup.finish().await;
}

#[tokio::test]
async fn store_torrent_series_with_files_links_episodes() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(
        pool,
        MediaType::Series,
        "stream_store::torrent_series_files",
    )
    .await;
    cleanup.media_ids.push(media_id);

    let info_hash = format!("ser{media_id:0>36}");
    let mut stream = sample_torrent(&info_hash);
    stream.files = vec![
        StreamFileStoreInput {
            file_index: 0,
            filename: "Show.S01E01.mkv".to_string(),
            size: Some(1000),
            season_number: 1,
            episode_number: 1,
        },
        StreamFileStoreInput {
            file_index: 1,
            filename: "Show.S01E02.mkv".to_string(),
            size: Some(1000),
            season_number: 1,
            episode_number: 2,
        },
    ];

    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series);
    let result = store_torrent_stream(pool, &stream, &opts)
        .await
        .expect("store");
    cleanup.stream_ids.push(result.stream_id().0);

    assert!(file_episode_link_exists(pool, result.stream_id().0, media_id, 1, 1).await);
    assert!(file_episode_link_exists(pool, result.stream_id().0, media_id, 1, 2).await);
    cleanup.finish().await;
}

#[tokio::test]
async fn store_torrent_series_files_default_episode_when_missing() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(
        pool,
        MediaType::Series,
        "stream_store::torrent_series_default_ep",
    )
    .await;
    cleanup.media_ids.push(media_id);

    let info_hash = format!("def{media_id:0>36}");
    let mut stream = sample_torrent(&info_hash);
    stream.files = vec![StreamFileStoreInput {
        file_index: 2,
        filename: "episode.mkv".to_string(),
        size: None,
        season_number: 0,
        episode_number: 0,
    }];

    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series);
    let result = store_torrent_stream(pool, &stream, &opts)
        .await
        .expect("store");
    cleanup.stream_ids.push(result.stream_id().0);

    // file_index 2 → episode 3 (1-based default)
    assert!(file_episode_link_exists(pool, result.stream_id().0, media_id, 1, 3).await);
    cleanup.finish().await;
}

#[tokio::test]
async fn store_torrent_series_pack_uses_synthetic_episode_from_opts() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Series, "stream_store::torrent_series_pack").await;
    cleanup.media_ids.push(media_id);

    let info_hash = format!("pack{media_id:0>36}");
    let stream = sample_torrent(&info_hash);

    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series)
        .with_episode(Some(2), Some(7));

    let result = store_torrent_stream(pool, &stream, &opts)
        .await
        .expect("store");
    cleanup.stream_ids.push(result.stream_id().0);

    assert_eq!(
        stream_media_link_count(pool, result.stream_id().0, media_id).await,
        1
    );
    assert!(file_episode_link_exists(pool, result.stream_id().0, media_id, 2, 7).await);
    cleanup.finish().await;
}

#[tokio::test]
async fn store_torrent_is_idempotent_and_refreshes_seeders() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Movie, "stream_store::torrent_idempotent").await;
    cleanup.media_ids.push(media_id);

    let info_hash = format!("idem{media_id:0>36}");
    let mut stream = sample_torrent(&info_hash);
    stream.seeders = Some(3);

    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Movie);
    let first = store_torrent_stream(pool, &stream, &opts)
        .await
        .expect("first");
    cleanup.stream_ids.push(first.stream_id().0);
    assert!(first.was_inserted());

    stream.seeders = Some(99);
    let second = store_torrent_stream(pool, &stream, &opts)
        .await
        .expect("second");
    assert!(matches!(second, StoreStreamResult::AlreadyExists(_)));
    assert_eq!(first.stream_id(), second.stream_id());

    let seeders: Option<i32> =
        sqlx::query_scalar("SELECT seeders FROM torrent_stream WHERE info_hash = $1")
            .bind(&info_hash)
            .fetch_one(pool)
            .await
            .expect("seeders");
    assert_eq!(seeders, Some(99));
    cleanup.finish().await;
}

// ─── Import file path (upsert_stream_file_row + link_file_to_media_episode) ───

#[tokio::test]
async fn import_file_helpers_link_series_episode() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Series, "stream_store::import_file_link").await;
    cleanup.media_ids.push(media_id);

    let info_hash = format!("imp{media_id:0>36}");
    let stream = sample_torrent(&info_hash);
    let opts = StoreStreamOpts::user_import(MediaId(media_id), MediaType::Series);
    let result = store_torrent_stream(pool, &stream, &opts)
        .await
        .expect("store torrent");
    let stream_id = result.stream_id();
    cleanup.stream_ids.push(stream_id.0);

    let file_row = StreamFileStoreInput {
        file_index: 0,
        filename: "S01E04.mkv".to_string(),
        size: Some(512),
        season_number: 0,
        episode_number: 0,
    };
    let file_id = upsert_stream_file_row(pool, stream_id, &file_row)
        .await
        .expect("file")
        .expect("file id");

    let (s, e) = resolve_series_episode_numbers(0, None, None);
    link_file_to_media_episode(
        pool,
        file_id,
        MediaId(media_id),
        s,
        e,
        LinkSource::User,
        true,
    )
    .await
    .expect("link");

    assert!(file_episode_link_exists(pool, stream_id.0, media_id, 1, 1).await);
    cleanup.finish().await;
}

// ─── Usenet ───────────────────────────────────────────────────────────────────

#[tokio::test]
async fn store_usenet_inserts_and_dedupes() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Movie, "stream_store::usenet").await;
    cleanup.media_ids.push(media_id);

    let guid = format!("nzb-{media_id}");
    let parsed = mediafusion_api::parser::parse_title("Release.1080p");
    let stream = UsenetStoreInput {
        base: StreamStoreBase::from_parsed("Release".to_string(), "indexer".to_string(), &parsed),
        nzb_guid: guid.clone(),
        nzb_url: "https://example/nzb".to_string(),
        size: 4096,
        indexer: "Test".to_string(),
        group_name: None,
        is_passworded: false,
        files: vec![],
    };
    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Movie);

    let first = store_usenet_stream(pool, &stream, &opts)
        .await
        .expect("first");
    cleanup.stream_ids.push(first.stream_id().0);
    assert!(first.was_inserted());

    let second = store_usenet_stream(pool, &stream, &opts)
        .await
        .expect("second");
    assert!(matches!(second, StoreStreamResult::AlreadyExists(_)));
    cleanup.finish().await;
}

// ─── Telegram ─────────────────────────────────────────────────────────────────

#[tokio::test]
async fn store_telegram_dedupes_by_chat_and_message() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Movie, "stream_store::telegram").await;
    cleanup.media_ids.push(media_id);

    let parsed = mediafusion_api::parser::parse_title("video.mkv");
    let stream = TelegramStoreInput {
        base: StreamStoreBase::from_parsed(
            "video.mkv".to_string(),
            "Telegram".to_string(),
            &parsed,
        ),
        chat_id: format!("chat-{media_id}"),
        chat_username: None,
        message_id: 42,
        file_name: "video.mkv".to_string(),
        size: 100,
        mime_type: Some("video/mp4".to_string()),
        file_id: None,
        file_unique_id: None,
        backup_chat_id: None,
        backup_message_id: None,
    };
    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Movie);

    let first = store_telegram_stream(pool, &stream, &opts)
        .await
        .expect("first");
    cleanup.stream_ids.push(first.stream_id().0);
    assert!(first.was_inserted());

    let second = store_telegram_stream(pool, &stream, &opts)
        .await
        .expect("second");
    assert!(matches!(second, StoreStreamResult::AlreadyExists(_)));
    cleanup.finish().await;
}

// ─── HTTP / YouTube / AceStream ───────────────────────────────────────────────

#[tokio::test]
async fn store_http_dedupes_by_url_per_media() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Movie, "stream_store::http").await;
    cleanup.media_ids.push(media_id);

    let url = format!("https://example/stream/{media_id}.m3u8");
    let stream = HttpStoreInput {
        base: StreamStoreBase {
            name: "Live".to_string(),
            source: "test".to_string(),
            ..Default::default()
        },
        url: url.clone(),
        format: Some("hls".to_string()),
        behavior_hints: None,
        drm_key_id: None,
        drm_key: None,
        extractor_name: None,
    };
    let opts = StoreStreamOpts::user_import(MediaId(media_id), MediaType::Movie);

    let first = store_http_stream(pool, &stream, &opts)
        .await
        .expect("first");
    cleanup.stream_ids.push(first.stream_id().0);
    let second = store_http_stream(pool, &stream, &opts)
        .await
        .expect("second");
    assert!(matches!(second, StoreStreamResult::AlreadyExists(_)));
    cleanup.finish().await;
}

#[tokio::test]
async fn store_youtube_dedupes_by_video_id() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Movie, "stream_store::youtube").await;
    cleanup.media_ids.push(media_id);

    let video_id = format!("vid{media_id}");
    let stream = YoutubeStoreInput {
        base: StreamStoreBase {
            name: "Clip".to_string(),
            source: "youtube".to_string(),
            ..Default::default()
        },
        video_id: video_id.clone(),
        channel_id: None,
        channel_name: None,
        duration_seconds: Some(120),
        is_live: false,
        is_premiere: false,
    };
    let opts = StoreStreamOpts::user_import(MediaId(media_id), MediaType::Movie);

    let first = store_youtube_stream(pool, &stream, &opts)
        .await
        .expect("first");
    cleanup.stream_ids.push(first.stream_id().0);
    let second = store_youtube_stream(pool, &stream, &opts)
        .await
        .expect("second");
    assert!(matches!(second, StoreStreamResult::AlreadyExists(_)));
    cleanup.finish().await;
}

#[tokio::test]
async fn store_acestream_inserts_and_links_media() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Series, "stream_store::acestream").await;
    cleanup.media_ids.push(media_id);

    let content_id = format!("ace{media_id}");
    let stream = AcestreamStoreInput {
        base: StreamStoreBase {
            name: "Ace".to_string(),
            source: "test".to_string(),
            ..Default::default()
        },
        content_id: content_id.clone(),
        info_hash: None,
    };
    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series);

    let result = store_acestream_stream(pool, &stream, &opts)
        .await
        .expect("store");
    cleanup.stream_ids.push(result.stream_id().0);
    assert!(result.was_inserted());
    assert_eq!(
        stream_media_link_count(pool, result.stream_id().0, media_id).await,
        1
    );
    cleanup.finish().await;
}

// ─── Post-metadata torrent files ──────────────────────────────────────────────

#[tokio::test]
async fn upsert_torrent_files_by_hash_enriches_existing_torrent() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);
    let media_id = insert_media(pool, MediaType::Movie, "stream_store::torrent_files").await;
    cleanup.media_ids.push(media_id);

    let info_hash = format!("enr{media_id:0>36}");
    let stream = sample_torrent(&info_hash);
    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Movie);
    let result = store_torrent_stream(pool, &stream, &opts)
        .await
        .expect("store");
    cleanup.stream_ids.push(result.stream_id().0);

    upsert_torrent_files_by_hash(
        pool,
        &info_hash,
        &[TorrentFileEntry {
            file_index: 0,
            filename: "movie.mkv".to_string(),
            size: 999,
            season: None,
            episode: None,
        }],
        LinkSource::TorrentMetadata,
    )
    .await
    .expect("enrich");

    let file_count: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM stream_file WHERE stream_id = $1")
            .bind(result.stream_id().0)
            .fetch_one(pool)
            .await
            .expect("count");
    assert_eq!(file_count, 1);
    cleanup.finish().await;
}
