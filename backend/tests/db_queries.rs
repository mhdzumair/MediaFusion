/// Integration tests for database query correctness.
///
/// These tests protect against two families of runtime bugs that static analysis
/// and `cargo sqlx prepare --check` cannot catch for non-macro `query_as` calls:
///
///   1. LEFT JOIN LATERAL NULL-decode — columns that are NOT NULL in their base
///      table are inferred as non-nullable, but a LEFT JOIN can return NULL when
///      there is no matching row.  These tests insert media rows with deliberately
///      missing images/ratings and verify the query returns `None` fields rather
///      than a decode error.
///
///   2. GROUP BY completeness — a non-aggregate column missing from GROUP BY
///      causes a Postgres error at runtime.  The playback-info test exercises
///      the exact query path that had this bug with `ts.torrent_file`.
///
/// Each test inserts data prefixed with "test_db_queries::" and deletes it in a
/// finally-block, so the test database is left clean even on assertion failure.
mod common;

// db::meta::get_media_meta returns the full Stremio MetaItem row (poster, rating, etc.)
// db::media::get_media_meta (re-exported as db::get_media_meta) returns a SearchMeta stub.
// Tests here cover the former.
use mediafusion_api::db::{
    catalog::get_watchlist_items,
    fetch_stream_playback_info,
    meta::{get_episodes, get_media_meta as get_full_meta},
    types::{MediaId, MediaType},
};
// ─── helpers ─────────────────────────────────────────────────────────────────

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
    sqlx::query_scalar::<_, i32>(
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

async fn link_imdb(pool: &sqlx::PgPool, media_id: i32, imdb_id: &str) {
    sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id, created_at)
         VALUES ($1, 'imdb', $2, NOW())",
    )
    .bind(media_id)
    .bind(imdb_id)
    .execute(pool)
    .await
    .expect("link imdb");
}

// ─── get_media_meta: NULL poster / background / rating ───────────────────────

/// A movie with no poster, no background image, and no IMDb rating must decode
/// without error and return `None` for those three nullable fields.
///
/// Protects against: LEFT JOIN LATERAL NULL-decode on `mi_poster.url`,
/// `mi_bg.url`, `mr.rating` — src/db/meta.rs `get_media_meta` (external-id path).
/// Regression for the error logged as:
///   "meta query [tt37532356]: error occurred while decoding column 13: unexpected null"
#[tokio::test]
async fn get_media_meta_null_poster_and_rating_external_id() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let media_id = insert_media(pool, MediaType::Movie, "test_db_queries::no_poster_movie").await;
    let imdb_id = format!("tt_test_{media_id}");
    link_imdb(pool, media_id, &imdb_id).await;

    // No media_image or media_rating rows — forces LEFT JOIN LATERAL to return NULL.
    let result = get_full_meta(pool, &imdb_id, "movie").await;

    let row = result.expect("query must succeed (not panic/warn on NULL lateral)");
    assert_eq!(row.title, "test_db_queries::no_poster_movie");
    assert!(
        row.poster_url.is_none(),
        "poster_url must be None — no media_image row"
    );
    assert!(
        row.background_url.is_none(),
        "background_url must be None — no media_image row"
    );
    assert!(
        row.imdb_rating.is_none(),
        "imdb_rating must be None — no media_rating row"
    );

    let mut cleanup = Cleanup::new(pool);
    cleanup.media_ids.push(media_id);
    cleanup.finish().await;
}

/// Same test via internal `mf{id}` lookup path.
#[tokio::test]
async fn get_media_meta_null_poster_and_rating_internal_id() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let media_id = insert_media(
        pool,
        MediaType::Movie,
        "test_db_queries::no_poster_internal",
    )
    .await;

    let result = get_full_meta(pool, &format!("mf{media_id}"), "movie").await;

    sqlx::query("DELETE FROM media WHERE id = $1")
        .bind(media_id)
        .execute(pool)
        .await
        .ok();

    let row = result.expect("query must succeed via internal-id path");
    assert!(row.poster_url.is_none());
    assert!(row.background_url.is_none());
    assert!(row.imdb_rating.is_none());
}

// ─── get_episodes: NULL thumbnail / NULL file-link ───────────────────────────

/// Episodes with no `episode_image` and no `file_media_link` must decode with
/// `thumbnail_url = None` and `media_id = None`.
///
/// Protects against: LEFT JOIN LATERAL NULL-decode on `ei.url` (column 5) and
/// LEFT JOIN NULL on `fml.media_id` — src/db/meta.rs `get_episodes`.
/// Regression for:
///   "episodes for media 200645: error occurred while decoding column 5: unexpected null"
#[tokio::test]
async fn get_episodes_null_thumbnail_and_null_file_link() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let media_id = insert_media(pool, MediaType::Series, "test_db_queries::no_thumb_series").await;

    sqlx::query(
        "INSERT INTO series_metadata (media_id, total_seasons, total_episodes, created_at, updated_at)
         VALUES ($1, 1, 2, NOW(), NOW())",
    )
    .bind(media_id)
    .execute(pool)
    .await
    .expect("series_metadata");

    let season_id: i32 = sqlx::query_scalar::<_, i32>(
        "INSERT INTO season (series_id, season_number, episode_count)
         VALUES ((SELECT id FROM series_metadata WHERE media_id = $1), 1, 2) RETURNING id",
    )
    .bind(media_id)
    .fetch_one(pool)
    .await
    .expect("season");

    for ep in [1i32, 2] {
        sqlx::query(
            "INSERT INTO episode (season_id, episode_number, title, is_user_created,
                                  is_user_addition, created_at, updated_at)
             VALUES ($1, $2, $3, false, false, NOW(), NOW())",
        )
        .bind(season_id)
        .bind(ep)
        .bind(format!("Episode {ep}"))
        .execute(pool)
        .await
        .expect("episode");
    }

    // No episode_image rows, no file_media_link rows.
    let rows = get_episodes(pool, MediaId(media_id)).await;

    sqlx::query("DELETE FROM media WHERE id = $1")
        .bind(media_id)
        .execute(pool)
        .await
        .ok();

    assert_eq!(
        rows.len(),
        2,
        "must return both episodes (not an empty vec from a decode error)"
    );
    for row in &rows {
        assert!(
            row.thumbnail_url.is_none(),
            "thumbnail_url must be None when no episode_image row exists (column 5 NULL)"
        );
        assert!(
            row.media_id.is_none(),
            "media_id must be None when no file_media_link row exists"
        );
    }
}

// ─── fetch_stream_playback_info: GROUP BY completeness ───────────────────────

/// Fetch playback info for a movie torrent (no season/episode).
///
/// Protects against: GROUP BY missing `ts.torrent_file` in the non-series
/// query path — src/db/streams.rs `fetch_stream_playback_info`.
/// Regression for:
///   "fetch_stream_playback_info error: column 'ts.torrent_file' must appear in GROUP BY"
#[tokio::test]
async fn fetch_stream_playback_info_movie_group_by_is_complete() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;

    let stream_id: i32 = sqlx::query_scalar::<_, i32>(
        r#"INSERT INTO stream (stream_type, name, source, is_active, is_blocked, is_public,
                               playback_count, is_remastered, is_upscaled, is_proper, is_repack,
                               is_extended, is_complete, is_dubbed, is_subbed, created_at)
           VALUES ('TORRENT', 'test_db_queries::playback_movie.mkv', 'test',
                   true, false, true, 0,
                   false, false, false, false, false, false, false, false, NOW())
           RETURNING id"#,
    )
    .fetch_one(pool)
    .await
    .expect("insert stream");

    // Use a unique hash to avoid conflicts with other test data.
    let info_hash = format!("test{stream_id:0>36}");

    // Include a non-NULL torrent_file to exercise the GROUP BY column that was missing.
    sqlx::query(
        r#"INSERT INTO torrent_stream (stream_id, info_hash, total_size, torrent_type,
                                       file_count, torrent_file, created_at)
           VALUES ($1, $2, 2147483648, 'PUBLIC', 1, '\xdeadbeef'::bytea, NOW())"#,
    )
    .bind(stream_id)
    .bind(&info_hash)
    .execute(pool)
    .await
    .expect("insert torrent_stream");

    let result = fetch_stream_playback_info(pool, &info_hash, None, None).await;

    sqlx::query("DELETE FROM stream WHERE id = $1")
        .bind(stream_id)
        .execute(pool)
        .await
        .ok();

    let info = result.expect(
        "query must succeed — GROUP BY was missing ts.torrent_file causing a Postgres error",
    );
    assert_eq!(info.name, "test_db_queries::playback_movie.mkv");
    assert_eq!(info.size_bytes, Some(2147483648));
    assert!(
        info.torrent_file.is_some(),
        "stored torrent_file bytes must be returned"
    );
}

// ─── get_watchlist_items: info_hash → media join ─────────────────────────────

/// A downloaded info_hash linked to media via torrent_stream must appear in
/// watchlist catalog results.
#[tokio::test]
async fn get_watchlist_items_resolves_info_hash_to_media() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let media_id = insert_media(pool, MediaType::Movie, "test_db_queries::watchlist_movie").await;

    sqlx::query("UPDATE media SET total_streams = 1, last_stream_added = NOW() WHERE id = $1")
        .bind(media_id)
        .execute(pool)
        .await
        .expect("bump total_streams");

    let stream_id: i32 = sqlx::query_scalar::<_, i32>(
        r#"INSERT INTO stream (stream_type, name, source, is_active, is_blocked, is_public,
                               playback_count, is_remastered, is_upscaled, is_proper, is_repack,
                               is_extended, is_complete, is_dubbed, is_subbed, created_at)
           VALUES ('TORRENT', 'test_db_queries::watchlist.mkv', 'test',
                   true, false, true, 0,
                   false, false, false, false, false, false, false, false, NOW())
           RETURNING id"#,
    )
    .fetch_one(pool)
    .await
    .expect("insert stream");

    // 40-char hex-style hash (matches real torrent info_hashes).
    let info_hash = format!("aa{:038x}", stream_id);

    sqlx::query(
        r#"INSERT INTO torrent_stream (stream_id, info_hash, total_size, torrent_type,
                                       file_count, created_at)
           VALUES ($1, $2, 1000, 'PUBLIC', 1, NOW())"#,
    )
    .bind(stream_id)
    .bind(&info_hash)
    .execute(pool)
    .await
    .expect("insert torrent_stream");

    sqlx::query(
        "INSERT INTO stream_media_link (stream_id, media_id, is_primary, is_verified, created_at)
         VALUES ($1, $2, true, false, NOW())",
    )
    .bind(stream_id)
    .bind(media_id)
    .execute(pool)
    .await
    .expect("insert stream_media_link");

    let rows = get_watchlist_items(
        pool,
        "movie",
        std::slice::from_ref(&info_hash),
        0,
        &[],
        &[],
        "latest",
        "desc",
    )
    .await;

    assert_eq!(rows.len(), 1, "expected one watchlist row for linked hash");
    assert_eq!(rows[0].media_id, MediaId(media_id));
    assert_eq!(rows[0].title, "test_db_queries::watchlist_movie");

    sqlx::query("DELETE FROM stream WHERE id = $1")
        .bind(stream_id)
        .execute(pool)
        .await
        .ok();
    sqlx::query("DELETE FROM media WHERE id = $1")
        .bind(media_id)
        .execute(pool)
        .await
        .ok();
}
