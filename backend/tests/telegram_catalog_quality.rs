mod common;

use mediafusion_api::{
    db::{
        self, HttpStoreInput, MediaId, MediaType, NormalizedMetadata, StoreMediaOpts,
        StoreStreamOpts, StreamStoreBase, TelegramStoreInput,
    },
    jobs::handlers::telegram_catalog_cleanup::{
        block_candidate, rejection_reason, scan_candidates,
    },
    scrapers::media_resolve::search_meta_for_telegram_feed,
};
use sqlx::PgPool;

async fn media(pool: &PgPool, title: &str) -> MediaId {
    db::store_media(
        pool,
        &NormalizedMetadata {
            media_type: MediaType::Movie,
            title: title.into(),
            ..Default::default()
        },
        StoreMediaOpts::default(),
    )
    .await
    .unwrap()
}

async fn telegram(pool: &PgPool, id: MediaId, name: &str, message_id: i32) {
    db::store_telegram_stream(
        pool,
        &TelegramStoreInput {
            base: StreamStoreBase {
                name: name.into(),
                source: "Telegram".into(),
                ..Default::default()
            },
            chat_id: "telegram_quality_test".into(),
            chat_username: None,
            message_id,
            file_name: name.into(),
            size: 100_000_000,
            mime_type: None,
            file_id: None,
            file_unique_id: None,
            document_id: None,
            backup_chat_id: None,
            backup_message_id: None,
        },
        &StoreStreamOpts::scraper(id, MediaType::Movie),
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn requires_real_matches_but_retains_credible_unresolved_titles() {
    let _guard = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    mediafusion_api::migrate::run(pool).await.unwrap();
    let http = reqwest::Client::new();
    let junk = media(pool, "ashleygrahampb050813-bgvid124").await;
    // An exact stale stub cannot bypass the new Telegram metadata requirement.
    let result = search_meta_for_telegram_feed(
        pool,
        &http,
        "ashleygrahampb050813-bgvid124",
        false,
        None,
        false,
        None,
        None,
        None,
        false,
        &[],
        "tmdb",
    )
    .await;
    assert!(result.is_none());
    // Test the year-constrained query as well as no-year matching.
    assert!(
        search_meta_for_telegram_feed(
            pool,
            &http,
            "ashleygrahampb050813-bgvid124",
            false,
            Some(2024),
            false,
            None,
            None,
            None,
            false,
            &[],
            "tmdb"
        )
        .await
        .is_none()
    );

    let verified = media(pool, "Uncommon Genuine Title").await;
    db::store_external_id(pool, verified.0, "imdb", "tt99999999999").await;
    let result = search_meta_for_telegram_feed(
        pool,
        &http,
        "Uncommon Genuine Title",
        false,
        None,
        false,
        None,
        None,
        None,
        false,
        &[],
        "tmdb",
    )
    .await
    .unwrap();
    assert_eq!(result.media_id, verified);
    // Trusted caption mapping rescues opaque filenames and keeps canonical title.
    let caption = search_meta_for_telegram_feed(
        pool,
        &http,
        "video_12345",
        false,
        None,
        false,
        Some("tt99999999999"),
        None,
        None,
        false,
        &[],
        "tmdb",
    )
    .await
    .unwrap();
    assert_eq!(caption.media_id, verified);
    assert_eq!(caption.title, "Uncommon Genuine Title");
    let unknown = search_meta_for_telegram_feed(
        pool,
        &http,
        "Entirely Unlisted Regional Feature",
        true,
        Some(2024),
        false,
        None,
        None,
        None,
        false,
        &[],
        "tmdb",
    )
    .await
    .unwrap();
    let ext: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM media_external_id WHERE media_id = $1")
        .bind(unknown.media_id)
        .fetch_one(pool)
        .await
        .unwrap();
    assert_eq!(ext, 0);
    sqlx::query("DELETE FROM media WHERE id = ANY($1)")
        .bind(vec![junk.0, verified.0, unknown.media_id.0])
        .execute(pool)
        .await
        .unwrap();
}

#[tokio::test]
async fn cleanup_is_reversible_and_protects_enriched_reviewed_and_credible_media() {
    let _guard = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    mediafusion_api::migrate::run(pool).await.unwrap();
    let junk = media(pool, "test_quality_junk").await;
    telegram(pool, junk, "ashleygrahampb050813-bgvid124.mp4", 1).await;
    let genuine = media(pool, "test_quality_genuine").await;
    telegram(pool, genuine, "Unlisted.Film.2024.1080p.mkv", 2).await;
    telegram(pool, genuine, "video_23456.mp4", 3).await;
    let provider = media(pool, "test_quality_provider").await;
    telegram(pool, provider, "video_34567.mp4", 4).await;
    db::store_external_id(pool, provider.0, "tmdb", "99999999999").await;
    let reviewed = media(pool, "test_quality_reviewed").await;
    telegram(pool, reviewed, "video_45678.mp4", 5).await;
    sqlx::query("UPDATE media SET is_user_created = true WHERE id = $1")
        .bind(reviewed)
        .execute(pool)
        .await
        .unwrap();
    let mixed = media(pool, "test_quality_mixed").await;
    telegram(pool, mixed, "video_56789.mp4", 6).await;
    db::store_http_stream(
        pool,
        &HttpStoreInput {
            base: StreamStoreBase {
                name: "Genuine HTTP source".into(),
                ..Default::default()
            },
            url: "https://example.invalid/quality-test.mp4".into(),
            format: None,
            behavior_hints: None,
            drm_key_id: None,
            drm_key: None,
            extractor_name: None,
        },
        &StoreStreamOpts::scraper(mixed, MediaType::Movie),
    )
    .await
    .unwrap();
    let verified_link = media(pool, "test_quality_verified_link").await;
    telegram(pool, verified_link, "video_67890.mp4", 7).await;
    sqlx::query("UPDATE stream_media_link SET is_verified = true WHERE media_id = $1")
        .bind(verified_link)
        .execute(pool)
        .await
        .unwrap();
    let rows = scan_candidates(pool, 0, 5000).await.unwrap();
    assert!(!rows.iter().any(|r| r.id == provider.0
        || r.id == reviewed.0
        || r.id == mixed.0
        || r.id == verified_link.0));
    assert!(rejection_reason(rows.iter().find(|r| r.id == genuine.0).unwrap()).is_none());
    let candidate = rows.iter().find(|r| r.id == junk.0).unwrap();
    let reason = rejection_reason(candidate).unwrap();
    let blocked: bool = sqlx::query_scalar("SELECT is_blocked FROM media WHERE id = $1")
        .bind(junk)
        .fetch_one(pool)
        .await
        .unwrap();
    assert!(!blocked, "scanning must never write");
    assert!(block_candidate(pool, candidate, reason).await.unwrap());
    assert!(!block_candidate(pool, candidate, reason).await.unwrap());
    let (blocked, count): (bool, i64) = sqlx::query_as(
        "SELECT is_blocked, (SELECT COUNT(*) FROM stream_media_link WHERE media_id = media.id) FROM media WHERE id = $1"
    ).bind(junk).fetch_one(pool).await.unwrap();
    assert!(blocked);
    assert_eq!(count, 1, "quarantine retains the original stream");
    // Enrichment between scan and apply must prevent quarantine.
    let candidate = rows.iter().find(|r| r.id == genuine.0).unwrap();
    db::store_external_id(pool, genuine.0, "tmdb", "99999999998").await;
    assert!(!block_candidate(pool, candidate, "test").await.unwrap());
    sqlx::query("DELETE FROM stream WHERE id IN (SELECT stream_id FROM stream_media_link WHERE media_id = ANY($1))")
        .bind(vec![junk.0, genuine.0, provider.0, reviewed.0, mixed.0, verified_link.0]).execute(pool).await.unwrap();
    sqlx::query("DELETE FROM media WHERE id = ANY($1)")
        .bind(vec![
            junk.0,
            genuine.0,
            provider.0,
            reviewed.0,
            mixed.0,
            verified_link.0,
        ])
        .execute(pool)
        .await
        .unwrap();
}
