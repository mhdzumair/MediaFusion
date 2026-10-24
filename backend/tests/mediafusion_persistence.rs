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

/// Concurrent same-hash writes must retain the winner's episode links.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_same_hash_stores_preserve_episode_playback() {
    let _db_lock = common::lock_db_tests().await;
    // The shared test pool has a single connection; use a separate multi-connection
    // pool to exercise overlapping SELECT/INSERT/ON CONFLICT statements.
    let url = std::env::var("TEST_DATABASE_URL").unwrap_or_else(|_| {
        "postgresql://mediafusion:mediafusion@127.0.0.1:5432/mediafusion".into()
    });
    let pool = PgPoolOptions::new()
        .max_connections(4)
        .connect(&url)
        .await
        .expect("connect to concurrent test database");
    mediafusion_api::migrate::run(&pool)
        .await
        .expect("migrations");

    let media_id: i32 = sqlx::query_scalar(
        "INSERT INTO media (type, title, adult, is_blocked, is_public, is_user_created, \
         total_streams, nudity_status, created_at) \
         VALUES ($1, 'test_persistence::concurrent_episode', false, false, true, false, 0, 'UNKNOWN', NOW()) \
         RETURNING id",
    )
    .bind(MediaType::Series)
    .fetch_one(&pool)
    .await
    .expect("insert series");

    let hash = format!("aa{:038x}", media_id);
    let stream = test_stream(hash.clone());
    let first_opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series).with_episode(
        Some(4),
        Some(7),
        None,
    );
    let second_opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series).with_episode(
        Some(4),
        Some(8),
        None,
    );

    // A SHARE table lock allows the initial SELECT but blocks INSERT's
    // ROW EXCLUSIVE lock. Wait until *both* writers are blocked at INSERT:
    // releasing it then necessarily makes one writer take ON CONFLICT.
    let mut blocker = pool.begin().await.expect("start blocking transaction");
    sqlx::query("LOCK TABLE torrent_stream IN SHARE MODE")
        .execute(&mut *blocker)
        .await
        .expect("block concurrent inserts");

    let first_pool = pool.clone();
    let first_stream = stream.clone();
    let first_task = tokio::spawn(async move {
        store_torrent_streams(
            &first_pool,
            std::slice::from_ref(&first_stream),
            &first_opts,
        )
        .await
    });
    let second_pool = pool.clone();
    let second_stream = stream.clone();
    let second_task = tokio::spawn(async move {
        store_torrent_streams(
            &second_pool,
            std::slice::from_ref(&second_stream),
            &second_opts,
        )
        .await
    });

    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    loop {
        let waiters: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM pg_stat_activity \
             WHERE datname = current_database() AND wait_event_type = 'Lock' \
             AND query LIKE '%INSERT INTO torrent_stream%'",
        )
        .fetch_one(&pool)
        .await
        .expect("observe blocked inserts");
        if waiters >= 2 {
            break;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "both writers must reach the torrent INSERT before releasing the lock"
        );
        tokio::time::sleep(std::time::Duration::from_millis(25)).await;
    }
    blocker.commit().await.expect("release insert lock");

    let first = first_task.await.expect("first concurrent task");
    let second = second_task.await.expect("second concurrent task");
    assert!(first.contains(&hash), "first concurrent store must succeed");
    assert!(
        second.contains(&hash),
        "second concurrent store must succeed"
    );

    let stored_rows: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM torrent_stream WHERE info_hash = $1")
            .bind(&hash)
            .fetch_one(&pool)
            .await
            .expect("count torrent rows");
    assert_eq!(
        stored_rows, 1,
        "concurrent insert must store only one torrent"
    );

    let episode_links: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM file_media_link fml \
         JOIN stream_file sf ON sf.id = fml.file_id \
         JOIN torrent_stream ts ON ts.stream_id = sf.stream_id \
         WHERE ts.info_hash = $1 AND fml.media_id = $2 \
         AND fml.season_number = 4 AND fml.episode_number IN (7, 8)",
    )
    .bind(&hash)
    .bind(media_id)
    .fetch_one(&pool)
    .await
    .expect("count episode links");
    assert_eq!(episode_links, 2, "both requested episodes must have links");
    for episode in [7, 8] {
        let playback = fetch_stream_playback_info(&pool, &hash, Some(4), Some(episode))
            .await
            .expect("each requested episode must resolve");
        assert!(
            !playback.has_no_files,
            "both requested episodes must have playback files"
        );
    }

    sqlx::query(
        "DELETE FROM stream WHERE id = (SELECT stream_id FROM torrent_stream WHERE info_hash = $1)",
    )
    .bind(&hash)
    .execute(&pool)
    .await
    .expect("clean torrent");
    sqlx::query("DELETE FROM media WHERE id = $1")
        .bind(media_id)
        .execute(&pool)
        .await
        .expect("clean media");
    pool.close().await;
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

#[tokio::test]
async fn rediscovery_preserves_physical_episode_mapping() {
    use mediafusion_api::db::{StreamFileStoreInput, store_torrent_stream};
    let _lock = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    mediafusion_api::migrate::run(pool).await.unwrap();
    let media_id: i32 = sqlx::query_scalar("INSERT INTO media (type,title,adult,is_blocked,is_public,is_user_created,total_streams,nudity_status,created_at) VALUES ($1,'review738::pack',false,false,true,false,0,'UNKNOWN',NOW()) RETURNING id")
        .bind(MediaType::Series).fetch_one(pool).await.unwrap();
    let hash = format!("f1{:038x}", media_id);
    let mut input = test_stream(hash.clone());
    input.files = vec![
        StreamFileStoreInput {
            file_index: 0,
            filename: "Show.S04E01.mkv".into(),
            size: Some(1000),
            season_number: 4,
            episode_number: 1,
        },
        StreamFileStoreInput {
            file_index: 7,
            filename: "Show.S04E08.mkv".into(),
            size: Some(1000),
            season_number: 4,
            episode_number: 8,
        },
    ];
    let opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series).with_episode(
        Some(4),
        Some(8),
        None,
    );
    let id = store_torrent_stream(pool, &input, &opts)
        .await
        .unwrap()
        .stream_id();
    let before = fetch_stream_playback_info(pool, &hash, Some(4), Some(8))
        .await
        .unwrap();
    assert_eq!(before.filename.as_deref(), Some("Show.S04E08.mkv"));
    // A real Prowlarr/title-only scraper result uses an unverified index-0 stub.
    input.files = vec![StreamFileStoreInput {
        file_index: 0,
        filename: String::new(),
        size: None,
        season_number: 4,
        episode_number: 8,
    }];
    assert!(
        store_torrent_streams(pool, std::slice::from_ref(&input), &opts)
            .await
            .contains(&hash)
    );
    let after = fetch_stream_playback_info(pool, &hash, Some(4), Some(8))
        .await
        .unwrap();
    let wrong_links: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM file_media_link f JOIN stream_file sf ON sf.id=f.file_id WHERE sf.stream_id=$1 AND sf.file_index=0 AND f.season_number=4 AND f.episode_number=8")
        .bind(id).fetch_one(pool).await.unwrap();
    assert_eq!(after.filename.as_deref(), Some("Show.S04E08.mkv"));
    // Repair a missing mapping from the stored filename, rather than the stub's index.
    sqlx::query("DELETE FROM file_media_link WHERE media_id=$1 AND episode_number=8")
        .bind(media_id)
        .execute(pool)
        .await
        .unwrap();
    assert!(
        store_torrent_streams(pool, std::slice::from_ref(&input), &opts)
            .await
            .contains(&hash)
    );
    let repaired = fetch_stream_playback_info(pool, &hash, Some(4), Some(8))
        .await
        .unwrap();
    assert_eq!(repaired.file_index, Some(7));

    // Also repair an index-zero mapping left by an earlier scraper rediscovery.
    sqlx::query("INSERT INTO file_media_link(file_id, media_id, season_number, episode_number, link_source, is_primary, confidence, created_at) SELECT id,$2,4,8,$3,true,1.0,NOW() FROM stream_file WHERE stream_id=$1 AND file_index=0")
        .bind(id).bind(media_id).bind(mediafusion_api::db::types::LinkSource::PttParser)
        .execute(pool).await.unwrap();
    assert!(
        store_torrent_streams(pool, std::slice::from_ref(&input), &opts)
            .await
            .contains(&hash)
    );
    let repaired = fetch_stream_playback_info(pool, &hash, Some(4), Some(8))
        .await
        .unwrap();
    assert_eq!(repaired.file_index, Some(7));

    let repaired_links: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM file_media_link WHERE media_id=$1 AND season_number=4 AND episode_number=8")
        .bind(media_id).fetch_one(pool).await.unwrap();
    assert_eq!(
        repaired_links, 1,
        "the older incorrect scraper link must be removed"
    );

    // Preserve an existing mapping when the filename is opaque.
    sqlx::query(
        "UPDATE stream_file SET filename='opaque-video.mkv' WHERE stream_id=$1 AND file_index=7",
    )
    .bind(id)
    .execute(pool)
    .await
    .unwrap();
    assert!(
        store_torrent_streams(pool, std::slice::from_ref(&input), &opts)
            .await
            .contains(&hash)
    );
    let opaque = fetch_stream_playback_info(pool, &hash, Some(4), Some(8))
        .await
        .unwrap();
    assert_eq!(opaque.file_index, Some(7));
    assert_eq!(opaque.filename.as_deref(), Some("opaque-video.mkv"));

    // An episode with no known physical file must not inherit file zero.
    input.files[0].episode_number = 9;
    let next_opts = StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series).with_episode(
        Some(4),
        Some(9),
        None,
    );
    assert!(
        store_torrent_streams(pool, &[input], &next_opts)
            .await
            .contains(&hash)
    );
    let unknown = fetch_stream_playback_info(pool, &hash, Some(4), Some(9))
        .await
        .unwrap();
    assert_eq!(unknown.file_index, Some(-1));
    assert_eq!(unknown.filename.as_deref(), Some(""));

    // Provider enrichment replaces the placeholder with the actual file.
    mediafusion_api::db::upsert_torrent_files_by_hash(
        pool,
        &hash,
        &[mediafusion_api::db::TorrentFileEntry {
            file_index: 8,
            filename: "Show.S04E09.mkv".into(),
            size: 1000,
            season: Some(4),
            episode: Some(9),
        }],
        mediafusion_api::db::types::LinkSource::TorrentMetadata,
    )
    .await
    .unwrap();
    let enriched = fetch_stream_playback_info(pool, &hash, Some(4), Some(9))
        .await
        .unwrap();
    assert_eq!(enriched.file_index, Some(8));
    assert_eq!(enriched.filename.as_deref(), Some("Show.S04E09.mkv"));
    let first = fetch_stream_playback_info(pool, &hash, Some(4), Some(1))
        .await
        .unwrap();
    assert_eq!(first.filename.as_deref(), Some("Show.S04E01.mkv"));
    sqlx::query("DELETE FROM stream WHERE id=$1")
        .bind(id)
        .execute(pool)
        .await
        .unwrap();
    sqlx::query("DELETE FROM media WHERE id=$1")
        .bind(media_id)
        .execute(pool)
        .await
        .unwrap();
    assert_eq!(
        wrong_links, 0,
        "rediscovery must not corrupt the verified file/episode mapping"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_link_repairs_do_not_duplicate_parent_links() {
    use mediafusion_api::db::store_torrent_stream;
    let _lock = common::lock_db_tests().await;
    let pool = PgPoolOptions::new()
        .max_connections(4)
        .connect(&std::env::var("TEST_DATABASE_URL").unwrap_or_else(|_| {
            "postgresql://mediafusion:mediafusion@127.0.0.1:5432/mediafusion".into()
        }))
        .await
        .unwrap();
    mediafusion_api::migrate::run(&pool).await.unwrap();
    let media_id: i32 = sqlx::query_scalar("INSERT INTO media (type,title,adult,is_blocked,is_public,is_user_created,total_streams,nudity_status,created_at) VALUES ($1,'review738::concurrent-repair',false,false,true,false,0,'UNKNOWN',NOW()) RETURNING id")
        .bind(MediaType::Series).fetch_one(&pool).await.unwrap();
    let hash = format!("f2{:038x}", media_id);
    let input = test_stream(hash.clone());
    // Reproduce an existing torrent with no media link, e.g. after a prior failed link.
    let id = store_torrent_stream(
        &pool,
        &input,
        &StoreStreamOpts::scraper(MediaId(0), MediaType::Series),
    )
    .await
    .unwrap()
    .stream_id();
    sqlx::query("CREATE FUNCTION review738_barrier() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock_shared(738, NEW.media_id); RETURN NEW; END $$").execute(&pool).await.unwrap();
    sqlx::query("CREATE TRIGGER review738_barrier BEFORE INSERT ON stream_media_link FOR EACH ROW EXECUTE FUNCTION review738_barrier()").execute(&pool).await.unwrap();
    let mut blocker = pool.begin().await.unwrap();
    sqlx::query("SELECT pg_advisory_xact_lock(738,$1)")
        .bind(media_id)
        .execute(&mut *blocker)
        .await
        .unwrap();
    let mut tasks = Vec::new();
    for episode in [7, 8] {
        let pool = pool.clone();
        let input = input.clone();
        tasks.push(tokio::spawn(async move {
            store_torrent_streams(
                &pool,
                &[input],
                &StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series).with_episode(
                    Some(4),
                    Some(episode),
                    None,
                ),
            )
            .await
        }));
    }
    tokio::time::timeout(std::time::Duration::from_secs(5),async {
        loop {
            let count: i64=sqlx::query_scalar("SELECT COUNT(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event='advisory'").fetch_one(&pool).await.unwrap();
            if count >= 2 { break; }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    }).await.unwrap();
    blocker.commit().await.unwrap();
    for task in tasks {
        assert!(task.await.unwrap().contains(&hash));
    }
    sqlx::query("DROP TRIGGER review738_barrier ON stream_media_link")
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("DROP FUNCTION review738_barrier()")
        .execute(&pool)
        .await
        .unwrap();
    let count: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_media_link WHERE stream_id=$1 AND media_id=$2",
    )
    .bind(id)
    .bind(media_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    let total: i32 = sqlx::query_scalar("SELECT total_streams FROM media WHERE id=$1")
        .bind(media_id)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(total, 1, "counter must count the torrent once");
    sqlx::query("DELETE FROM stream WHERE id=$1")
        .bind(id)
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("DELETE FROM media WHERE id=$1")
        .bind(media_id)
        .execute(&pool)
        .await
        .unwrap();
    pool.close().await;
    assert_eq!(
        count, 1,
        "concurrent repair must not duplicate the parent stream/media link"
    );
}
