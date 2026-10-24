//! Duplicate analysis must reuse stored mappings without changing the torrent.
mod common;

use mediafusion_api::{
    db::{
        self, LinkSource, MediaId, MediaType, StoreStreamOpts, StreamStoreBase, TorrentFileEntry,
        TorrentStoreInput, TorrentType,
    },
    routes::content::torrent_import::existing_torrent_analysis,
};

#[tokio::test]
async fn duplicate_analysis_preserves_files_and_hides_private_details() {
    let _lock = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    mediafusion_api::migrate::run(pool).await.unwrap();
    let media_id: i32 = sqlx::query_scalar(
        "INSERT INTO media (type,title,adult,is_blocked,is_public,is_user_created,total_streams,nudity_status,created_at) \
         VALUES ($1,'Duplicate analysis regression',false,false,true,false,0,'UNKNOWN',NOW()) RETURNING id",
    ).bind(MediaType::Series).fetch_one(pool).await.unwrap();
    let hash = format!("{media_id:040x}");
    let parsed = mediafusion_api::parser::parse_title("Regression.S01.1080p");
    let torrent = TorrentStoreInput {
        base: StreamStoreBase::from_parsed("Regression.S01.1080p".into(), "test".into(), &parsed),
        info_hash: hash.clone(),
        total_size: 2000,
        seeders: None,
        torrent_type: TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
        files: vec![],
    };
    let stream_id = db::store_torrent_stream(
        pool,
        &torrent,
        &StoreStreamOpts::scraper(MediaId(media_id), MediaType::Series),
    )
    .await
    .unwrap()
    .stream_id();
    db::upsert_torrent_files_by_hash(
        pool,
        &hash,
        &[TorrentFileEntry {
            file_index: 7,
            filename: "Regression.S01E02.mkv".into(),
            size: 2000,
            season: Some(1),
            episode: Some(2),
        }],
        LinkSource::TorrentMetadata,
    )
    .await
    .unwrap();
    let before: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM stream_file WHERE stream_id=$1")
        .bind(stream_id)
        .fetch_one(pool)
        .await
        .unwrap();
    let result = existing_torrent_analysis(pool, &hash, None)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result["already_exists"], true);
    assert_eq!(result["existing_stream_id"], stream_id.0);
    assert_eq!(result["resolution"], "1080p");
    assert_eq!(
        result["files"].as_array().unwrap().len(),
        1,
        "ignore synthetic placeholders"
    );
    assert_eq!(result["files"][0]["index"], 7);
    assert_eq!(result["files"][0]["linked_media_id"], media_id);
    assert_eq!(result["files"][0]["episode_number"], 2);
    assert_eq!(result["attached_media"][0]["media_id"], media_id);
    assert!(
        existing_torrent_analysis(pool, "missing", None)
            .await
            .unwrap()
            .is_none()
    );
    let after: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM stream_file WHERE stream_id=$1")
        .bind(stream_id)
        .fetch_one(pool)
        .await
        .unwrap();
    assert_eq!(before, after);
    for (public, blocked) in [(false, false), (true, true)] {
        sqlx::query("UPDATE stream SET is_public=$2,is_blocked=$3 WHERE id=$1")
            .bind(stream_id)
            .bind(public)
            .bind(blocked)
            .execute(pool)
            .await
            .unwrap();
        let hidden = existing_torrent_analysis(pool, &hash, None)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(hidden["already_exists"], true);
        assert!(hidden.get("existing_stream_id").is_none());
        assert!(hidden.get("torrent_name").is_none());
        assert_eq!(hidden["files"], serde_json::json!([]));
        assert!(hidden.get("attached_media").is_none());
    }
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
