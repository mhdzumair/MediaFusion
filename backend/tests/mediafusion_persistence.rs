mod common;

use mediafusion_api::db::{
    MediaId, MediaType, StoreStreamOpts, StreamStoreBase, TorrentStoreInput, TorrentType,
    fetch_stream_playback_info, store_torrent_streams,
};
use sqlx::postgres::PgPoolOptions;

/// Build a small synthetic torrent input without external tracker traffic.
fn test_stream(info_hash: String) -> TorrentStoreInput {
    TorrentStoreInput {
        base: StreamStoreBase {
            name: "MediaFusion.Persistence.Test.S04.2160p".into(),
            source: "integration_test".into(),
            ..Default::default()
        },
        info_hash,
        total_size: 1024,
        seeders: None,
        torrent_type: TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
        files: vec![],
    }
}

/// A failed PostgreSQL write must never be treated as a playable stream.
#[tokio::test]
async fn failed_db_insert_never_returns_a_playback_hash() {
    let pool = PgPoolOptions::new()
        .acquire_timeout(std::time::Duration::from_millis(250))
        .connect_lazy("postgresql://nobody:nobody@127.0.0.1:1/unreachable")
        .expect("valid URL");
    let opts = StoreStreamOpts::scraper(MediaId(1), MediaType::Movie);
    let input = test_stream("abcdef0123456789abcdef0123456789abcdef01".into());
    let stored = store_torrent_streams(&pool, &[input], &opts).await;
    assert!(
        stored.is_empty(),
        "failed writes cannot be returned to Stremio"
    );
    pool.close().await;
}

/// Rediscovered season-pack hashes gain the requested episode link.
#[tokio::test]
async fn existing_hash_is_linked_to_a_new_series_episode() {
    let _db_lock = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    mediafusion_api::migrate::run(pool)
        .await
        .expect("set up isolated test database");

    let media_id: i32 = sqlx::query_scalar(
        "INSERT INTO media (type, title, adult, is_blocked, is_public, is_user_created, \
          total_streams, nudity_status, created_at) \
          VALUES ($1, 'test_persistence::series', false, false, true, false, 0, 'UNKNOWN', NOW()) \
          RETURNING id",
    )
    .bind(MediaType::Series)
    .fetch_one(pool)
    .await
    .expect("insert series");

    let info_hash = format!("ab{:038x}", media_id);
    let input = test_stream(info_hash.clone());
    let first_opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series).with_episode(
        Some(4),
        Some(7),
        None,
    );
    let first = store_torrent_streams(pool, std::slice::from_ref(&input), &first_opts).await;
    assert!(first.contains(&info_hash), "initial write must succeed");

    let next_opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series).with_episode(
        Some(4),
        Some(8),
        None,
    );
    let second = store_torrent_streams(pool, std::slice::from_ref(&input), &next_opts).await;
    assert!(
        second.contains(&info_hash),
        "existing hash must be relinked for next episode"
    );

    let episode_link_count: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM file_media_link fml \
         JOIN stream_file sf ON sf.id = fml.file_id \
         JOIN torrent_stream ts ON ts.stream_id = sf.stream_id \
         WHERE ts.info_hash = $1 AND fml.media_id = $2 \
         AND fml.season_number = 4 AND fml.episode_number = 8",
    )
    .bind(&info_hash)
    .bind(media_id)
    .fetch_one(pool)
    .await
    .expect("read stored episode link");
    assert_eq!(
        episode_link_count, 1,
        "S04E08 must have exactly one stored file link"
    );

    let playback = fetch_stream_playback_info(pool, &info_hash, Some(4), Some(8))
        .await
        .expect("stored hash must resolve at playback time");
    assert!(
        !playback.has_no_files,
        "episode metadata must be available at playback"
    );

    sqlx::query(
        "DELETE FROM stream WHERE id = (SELECT stream_id FROM torrent_stream WHERE info_hash = $1)",
    )
    .bind(&info_hash)
    .execute(pool)
    .await
    .expect("clean stream");
    sqlx::query("DELETE FROM media WHERE id = $1")
        .bind(media_id)
        .execute(pool)
        .await
        .expect("clean media");
}

/// First insertion and repeat discovery both preserve playable hash lookup.
#[tokio::test]
async fn successful_movie_insert_and_idempotent_retry_are_resolvable() {
    let _db_lock = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    mediafusion_api::migrate::run(pool)
        .await
        .expect("migrations");

    let media_id: i32 = sqlx::query_scalar(
        "INSERT INTO media (type, title, adult, is_blocked, is_public, is_user_created, \
         total_streams, nudity_status, created_at) \
         VALUES ($1, 'test_persistence::movie', false, false, true, false, 0, 'UNKNOWN', NOW()) \
         RETURNING id",
    )
    .bind(MediaType::Movie)
    .fetch_one(pool)
    .await
    .expect("insert media");

    let hash = format!("cd{:038x}", media_id);
    let stream = test_stream(hash.clone());
    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Movie);
    for _ in 0..2 {
        let stored = store_torrent_streams(pool, std::slice::from_ref(&stream), &opts).await;
        assert!(
            stored.contains(&hash),
            "insert and retry must return playable hash"
        );
        assert!(
            fetch_stream_playback_info(pool, &hash, None, None)
                .await
                .is_some(),
            "successful writes must resolve through the primary database"
        );
    }
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM torrent_stream WHERE info_hash = $1")
        .bind(&hash)
        .fetch_one(pool)
        .await
        .expect("count stored streams");
    assert_eq!(count, 1, "retry must not duplicate the torrent");
    sqlx::query(
        "DELETE FROM stream WHERE id = (SELECT stream_id FROM torrent_stream WHERE info_hash = $1)",
    )
    .bind(&hash)
    .execute(pool)
    .await
    .expect("clean torrent");
    sqlx::query("DELETE FROM media WHERE id = $1")
        .bind(media_id)
        .execute(pool)
        .await
        .expect("clean media");
}

/// A missing episode FK fails cleanly and a later valid retry repairs it.
#[tokio::test]
async fn failed_episode_link_does_not_advertise_hash_and_retry_repairs_it() {
    let _db_lock = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    mediafusion_api::migrate::run(pool)
        .await
        .expect("migrations");

    let media_id: i32 = sqlx::query_scalar(
        "INSERT INTO media (type, title, adult, is_blocked, is_public, is_user_created, \
         total_streams, nudity_status, created_at) \
         VALUES ($1, 'test_persistence::failed_link', false, false, true, false, 0, 'UNKNOWN', NOW()) \
         RETURNING id",
    )
    .bind(MediaType::Series)
    .fetch_one(pool)
    .await
    .expect("insert series");
    let hash = format!("ef{:038x}", media_id);
    let stream = test_stream(hash.clone());

    // Force a real PostgreSQL FK failure after torrent_stream is inserted.
    let invalid_opts = StoreStreamOpts::scraper(MediaId(2147483000), MediaType::Series)
        .with_episode(Some(4), Some(8), None);
    let failure = store_torrent_streams(pool, std::slice::from_ref(&stream), &invalid_opts).await;
    assert!(
        !failure.contains(&hash),
        "hash cannot be advertised when linking its requested episode failed"
    );

    let valid_opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series).with_episode(
        Some(4),
        Some(8),
        None,
    );
    let repaired = store_torrent_streams(pool, std::slice::from_ref(&stream), &valid_opts).await;
    assert!(
        repaired.contains(&hash),
        "retry must repair the missing episode link"
    );
    let playback = fetch_stream_playback_info(pool, &hash, Some(4), Some(8))
        .await
        .expect("repair must restore playback metadata");
    assert!(
        !playback.has_no_files,
        "repaired episode must have a file link"
    );

    sqlx::query(
        "DELETE FROM stream WHERE id = (SELECT stream_id FROM torrent_stream WHERE info_hash = $1)",
    )
    .bind(&hash)
    .execute(pool)
    .await
    .expect("clean stream");
    sqlx::query("DELETE FROM media WHERE id = $1")
        .bind(media_id)
        .execute(pool)
        .await
        .expect("clean media");
}
