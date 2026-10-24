mod common;

use mediafusion_api::{db, parser};

#[tokio::test]
async fn regional_editions_keep_distinct_media_and_torznab_imdb_ids() {
    let _lock = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    mediafusion_api::migrate::run(pool).await.unwrap();
    let mut stored = Vec::new();
    for (show, imdb_id) in [
        ("Bigg Boss (Tamil)", "tt7084920"),
        ("Bigg Boss (Telugu)", "tt7805494"),
        ("Big Brother (US)", "tt990000001"),
        ("Big Brother (UK)", "tt990000002"),
        (
            "A Shared Long Reality Competition Series Title (US)",
            "tt990000003",
        ),
        (
            "A Shared Long Reality Competition Series Title (UK)",
            "tt990000004",
        ),
    ] {
        let name = format!("{show} S10 EP21 DAY 20 TRUE WEB-DL - 720p - AVC - AAC - 350MB.mkv");
        let parsed = parser::parse_title(&name);
        let media_id = db::store_media(
            pool,
            &db::NormalizedMetadata {
                media_type: db::MediaType::Series,
                title: parsed.title.unwrap(),
                year: Some(2017),
                external_ids: vec![("imdb".into(), imdb_id.into())],
                ..Default::default()
            },
            db::StoreMediaOpts::default(),
        )
        .await
        .unwrap();
        let hash = format!("a7{:038x}", media_id.0);
        let torrent = db::TorrentStoreInput {
            base: db::StreamStoreBase {
                name,
                source: "regional_identity_test".into(),
                ..Default::default()
            },
            info_hash: hash.clone(),
            total_size: 1024,
            seeders: Some(1),
            torrent_type: db::TorrentType::Public,
            torrent_file: None,
            announce_list: vec![],
            files: vec![],
        };
        let stream_id = db::store_torrent_stream(
            pool,
            &torrent,
            &db::StoreStreamOpts::scraper(media_id, db::MediaType::Series).with_episode(
                Some(10),
                Some(21),
                None,
            ),
        )
        .await
        .unwrap()
        .stream_id();
        stored.push((media_id, stream_id, hash, imdb_id));
    }
    let media_ids = stored
        .iter()
        .map(|(id, _, _, _)| *id)
        .collect::<std::collections::HashSet<_>>();
    assert_eq!(
        media_ids.len(),
        stored.len(),
        "regional editions must not be merged, even when fuzzy title scores are high"
    );
    for (_, _, hash, imdb_id) in &stored {
        let rows =
            db::torznab::search_by_imdb(pool, imdb_id, Some("series"), Some(10), Some(21), 10)
                .await;
        assert_eq!(rows.len(), 1);
        assert_eq!(&rows[0].info_hash, hash);
        assert_eq!(rows[0].imdb_id.as_deref(), Some(*imdb_id));
    }
    for (media_id, stream_id, _, _) in stored {
        sqlx::query("DELETE FROM stream WHERE id=$1")
            .bind(stream_id)
            .execute(pool)
            .await
            .unwrap();
        sqlx::query("DELETE FROM media WHERE id=$1")
            .bind(media_id)
            .execute(pool)
            .await
            .unwrap();
    }
}
