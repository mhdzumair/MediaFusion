mod common;

use mediafusion_api::db::set_external_id_for_media;
use mediafusion_api::db::types::MediaType;

struct Cleanup {
    pool: &'static sqlx::PgPool,
    media_ids: Vec<i32>,
}

impl Cleanup {
    fn new(pool: &'static sqlx::PgPool) -> Self {
        Self {
            pool,
            media_ids: vec![],
        }
    }

    async fn finish(self) {
        if !self.media_ids.is_empty() {
            let _ = sqlx::query("DELETE FROM media WHERE id = ANY($1)")
                .bind(&self.media_ids)
                .execute(self.pool)
                .await;
        }
    }
}

async fn insert_media(pool: &sqlx::PgPool, title: &str) -> i32 {
    sqlx::query_scalar::<_, i32>(
        r#"INSERT INTO media (type, title, adult, is_blocked, is_public, is_user_created,
                              total_streams, nudity_status, created_at)
           VALUES ($1, $2, false, false, true, false, 0, 'UNKNOWN', NOW())
           RETURNING id"#,
    )
    .bind(MediaType::Movie)
    .bind(title)
    .fetch_one(pool)
    .await
    .expect("insert media")
}

async fn imdb_ids_for_media(pool: &sqlx::PgPool, media_id: i32) -> Vec<String> {
    sqlx::query_scalar(
        "SELECT external_id FROM media_external_id WHERE media_id = $1 AND provider = 'imdb' ORDER BY external_id",
    )
    .bind(media_id)
    .fetch_all(pool)
    .await
    .expect("fetch imdb ids")
}

#[tokio::test]
async fn set_external_id_for_media_replaces_existing_provider_id() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);

    let media_id = insert_media(pool, "test_set_external_id::replace").await;
    cleanup.media_ids.push(media_id);

    let old_id = format!("tt_old_{media_id}");
    let new_id = format!("tt_new_{media_id}");

    sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id, created_at)
         VALUES ($1, 'imdb', $2, NOW())",
    )
    .bind(media_id)
    .bind(&old_id)
    .execute(pool)
    .await
    .expect("seed old imdb id");

    assert!(
        set_external_id_for_media(pool, media_id, "imdb", &new_id).await,
        "replacement should succeed"
    );

    let ids = imdb_ids_for_media(pool, media_id).await;
    assert_eq!(ids, vec![new_id]);

    cleanup.finish().await;
}

#[tokio::test]
async fn set_external_id_for_media_cleans_duplicate_provider_rows() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);

    let media_id = insert_media(pool, "test_set_external_id::dedupe").await;
    cleanup.media_ids.push(media_id);

    let old_id = format!("tt_old_{media_id}");
    let new_id = format!("tt_new_{media_id}");

    for imdb_id in [&old_id, &new_id] {
        sqlx::query(
            "INSERT INTO media_external_id (media_id, provider, external_id, created_at)
             VALUES ($1, 'imdb', $2, NOW())",
        )
        .bind(media_id)
        .bind(imdb_id)
        .execute(pool)
        .await
        .expect("seed duplicate imdb ids");
    }

    assert!(
        set_external_id_for_media(pool, media_id, "imdb", &new_id).await,
        "dedupe should succeed when target id already belongs to media"
    );

    let ids = imdb_ids_for_media(pool, media_id).await;
    assert_eq!(ids, vec![new_id]);

    cleanup.finish().await;
}

#[tokio::test]
async fn set_external_id_for_media_rejects_id_owned_by_other_media() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;
    let mut cleanup = Cleanup::new(pool);

    let owner_id = insert_media(pool, "test_set_external_id::owner").await;
    let target_id = insert_media(pool, "test_set_external_id::target").await;
    cleanup.media_ids.extend([owner_id, target_id]);

    let shared_id = format!("tt_shared_{owner_id}");
    sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id, created_at)
         VALUES ($1, 'imdb', $2, NOW())",
    )
    .bind(owner_id)
    .bind(&shared_id)
    .execute(pool)
    .await
    .expect("seed shared imdb id");

    assert!(
        !set_external_id_for_media(pool, target_id, "imdb", &shared_id).await,
        "must not steal an external id from another media row"
    );

    assert_eq!(
        imdb_ids_for_media(pool, target_id).await,
        Vec::<String>::new()
    );

    cleanup.finish().await;
}
