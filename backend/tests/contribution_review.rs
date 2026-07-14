/// Regression tests for moderator contribution review enum binding.
mod common;

use mediafusion_api::db::ContributionStatus;
use uuid::Uuid;

async fn insert_test_contribution(pool: &sqlx::PgPool, id: &str) {
    sqlx::query(
        "INSERT INTO contributions (id, contribution_type, data, status, admin_review_requested, created_at)
         VALUES ($1, 'metadata', '{}', $2, false, NOW())",
    )
    .bind(id)
    .bind(ContributionStatus::Pending)
    .execute(pool)
    .await
    .expect("insert contribution");
}

/// Binding `ContributionStatus` into `contributions.status` must succeed.
/// Regression for: column "status" is of type contributionstatus but expression is of type text
#[tokio::test]
async fn contribution_status_update_binds_enum() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;

    let id = format!("test_contrib_review_{}", Uuid::new_v4());
    insert_test_contribution(pool, &id).await;

    sqlx::query(
        "UPDATE contributions SET status = $1, reviewed_by = $2, reviewed_at = NOW() WHERE id = $3",
    )
    .bind(ContributionStatus::Approved)
    .bind("1")
    .bind(&id)
    .execute(pool)
    .await
    .expect("update contribution status with enum bind");

    let status: ContributionStatus =
        sqlx::query_scalar("SELECT status FROM contributions WHERE id = $1")
            .bind(&id)
            .fetch_one(pool)
            .await
            .expect("fetch status");

    assert_eq!(status, ContributionStatus::Approved);

    sqlx::query("DELETE FROM contributions WHERE id = $1")
        .bind(&id)
        .execute(pool)
        .await
        .ok();
}

/// Bulk-review style update: pending guard + enum bind for approved/rejected.
#[tokio::test]
async fn contribution_bulk_review_update_binds_enum() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;

    let id = format!("test_contrib_bulk_{}", Uuid::new_v4());
    insert_test_contribution(pool, &id).await;

    let result = sqlx::query(
        "UPDATE contributions SET status = $1, reviewed_by = $2, reviewed_at = NOW()
         WHERE id = $3 AND status = $4",
    )
    .bind(ContributionStatus::Rejected)
    .bind("99")
    .bind(&id)
    .bind(ContributionStatus::Pending)
    .execute(pool)
    .await
    .expect("bulk-style update");

    assert_eq!(result.rows_affected(), 1);

    let status: ContributionStatus =
        sqlx::query_scalar("SELECT status FROM contributions WHERE id = $1")
            .bind(&id)
            .fetch_one(pool)
            .await
            .expect("fetch status");

    assert_eq!(status, ContributionStatus::Rejected);

    sqlx::query("DELETE FROM contributions WHERE id = $1")
        .bind(&id)
        .execute(pool)
        .await
        .ok();
}

async fn insert_test_user(pool: &sqlx::PgPool, suffix: &str) -> i32 {
    let email = format!("test_contrib_{suffix}@example.com");
    let uuid = Uuid::new_v4().to_string();
    sqlx::query_scalar::<_, i32>(
        r#"INSERT INTO users (
               uuid, email, username, role, is_verified, is_active,
               contribution_points, metadata_edits_approved, stream_edits_approved,
               contribution_level, contribute_anonymously, uploads_restricted, created_at
           ) VALUES ($1, $2, $3, 'USER', true, true, 0, 0, 0, 'new', false, false, NOW())
           RETURNING id"#,
    )
    .bind(&uuid)
    .bind(&email)
    .bind(format!("test_user_{suffix}"))
    .fetch_one(pool)
    .await
    .expect("insert user")
}

async fn insert_test_stream(pool: &sqlx::PgPool) -> i32 {
    insert_test_stream_for_user(pool, None).await
}

async fn insert_test_stream_for_user(pool: &sqlx::PgPool, uploader_user_id: Option<i32>) -> i32 {
    sqlx::query_scalar::<_, i32>(
        r#"INSERT INTO stream (
               stream_type, name, source, is_active, is_blocked, is_public, playback_count,
               uploader_user_id,
               is_remastered, is_upscaled, is_proper, is_repack, is_extended,
               is_complete, is_dubbed, is_subbed, created_at
           ) VALUES ('HTTP', 'test stream', 'test', true, false, true, 0,
                     $1,
                     false, false, false, false, false, false, false, false, NOW())
           RETURNING id"#,
    )
    .bind(uploader_user_id)
    .fetch_one(pool)
    .await
    .expect("insert stream")
}

/// `stream_suggestions.status` is varchar with lowercase values; stats queries must match.
#[tokio::test]
async fn stream_suggestion_stats_use_lowercase_status() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;

    let suffix = Uuid::new_v4().simple().to_string();
    let user_id = insert_test_user(pool, &suffix).await;
    let stream_id = insert_test_stream(pool).await;
    let suggestion_id = format!("test_stream_sugg_{suffix}");

    sqlx::query(
        r#"INSERT INTO stream_suggestions (
               id, user_id, stream_id, suggestion_type, status, created_at
           ) VALUES ($1, $2, $3, 'field_change', 'pending', NOW())"#,
    )
    .bind(&suggestion_id)
    .bind(user_id)
    .bind(stream_id)
    .execute(pool)
    .await
    .expect("insert stream suggestion");

    let pending: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestions WHERE user_id = $1 AND status = 'pending'",
    )
    .bind(user_id)
    .fetch_one(pool)
    .await
    .expect("count pending");

    assert!(
        pending >= 1,
        "lowercase 'pending' must match stream_suggestions rows"
    );

    let uppercase_pending: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestions WHERE user_id = $1 AND status = 'PENDING'",
    )
    .bind(user_id)
    .fetch_one(pool)
    .await
    .expect("count uppercase pending");

    assert_eq!(
        uppercase_pending, 0,
        "uppercase 'PENDING' must not match lowercase stream_suggestions status"
    );

    sqlx::query("DELETE FROM stream_suggestions WHERE id = $1")
        .bind(&suggestion_id)
        .execute(pool)
        .await
        .ok();
    sqlx::query("DELETE FROM stream WHERE id = $1")
        .bind(stream_id)
        .execute(pool)
        .await
        .ok();
    sqlx::query("DELETE FROM users WHERE id = $1")
        .bind(user_id)
        .execute(pool)
        .await
        .ok();
}

/// Owner streams are listed only when `uploader_user_id` matches; anonymous streams are excluded.
#[tokio::test]
async fn owner_streams_list_filters_by_uploader_user_id() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;

    let suffix = Uuid::new_v4().simple().to_string();
    let owner_id = insert_test_user(pool, &format!("owner_{suffix}")).await;
    let other_id = insert_test_user(pool, &format!("other_{suffix}")).await;

    let owned_stream_id = insert_test_stream_for_user(pool, Some(owner_id)).await;
    let anonymous_stream_id = insert_test_stream_for_user(pool, None).await;
    let other_stream_id = insert_test_stream_for_user(pool, Some(other_id)).await;

    let owned_count: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM stream WHERE uploader_user_id = $1")
            .bind(owner_id)
            .fetch_one(pool)
            .await
            .expect("count owned streams");

    assert_eq!(owned_count, 1);

    let anonymous_in_owner_list: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM stream WHERE uploader_user_id = $1 AND id = $2")
            .bind(owner_id)
            .bind(anonymous_stream_id)
            .fetch_one(pool)
            .await
            .expect("count anonymous in owner list");

    assert_eq!(anonymous_in_owner_list, 0);

    let other_owner_count: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM stream WHERE uploader_user_id = $1 AND id = $2")
            .bind(owner_id)
            .bind(other_stream_id)
            .fetch_one(pool)
            .await
            .expect("count other user's stream in owner list");

    assert_eq!(other_owner_count, 0);

    for stream_id in [owned_stream_id, anonymous_stream_id, other_stream_id] {
        sqlx::query("DELETE FROM stream WHERE id = $1")
            .bind(stream_id)
            .execute(pool)
            .await
            .ok();
    }
    for user_id in [owner_id, other_id] {
        sqlx::query("DELETE FROM users WHERE id = $1")
            .bind(user_id)
            .execute(pool)
            .await
            .ok();
    }
}

/// Owner block is one-way: sets blocked + inactive; owner cannot self-unblock.
#[tokio::test]
async fn owner_block_is_one_way() {
    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;

    let suffix = Uuid::new_v4().simple().to_string();
    let owner_id = insert_test_user(pool, &format!("block_{suffix}")).await;
    let stream_id = insert_test_stream_for_user(pool, Some(owner_id)).await;

    sqlx::query(
        "UPDATE stream SET is_blocked = true, is_active = false WHERE id = $1 AND uploader_user_id = $2",
    )
    .bind(stream_id)
    .bind(owner_id)
    .execute(pool)
    .await
    .expect("block stream");

    let (is_blocked, is_active): (bool, bool) =
        sqlx::query_as("SELECT is_blocked, is_active FROM stream WHERE id = $1")
            .bind(stream_id)
            .fetch_one(pool)
            .await
            .expect("fetch blocked state");

    assert!(is_blocked);
    assert!(!is_active);

    sqlx::query("DELETE FROM stream WHERE id = $1")
        .bind(stream_id)
        .execute(pool)
        .await
        .ok();
    sqlx::query("DELETE FROM users WHERE id = $1")
        .bind(owner_id)
        .execute(pool)
        .await
        .ok();
}

/// Direct owner field updates mutate stream rows immediately.
#[tokio::test]
async fn owner_direct_edit_updates_stream_fields() {
    use mediafusion_api::routes::content::stream_suggestions::apply_stream_field_change;

    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;

    let suffix = Uuid::new_v4().simple().to_string();
    let owner_id = insert_test_user(pool, &format!("edit_{suffix}")).await;
    let stream_id = insert_test_stream_for_user(pool, Some(owner_id)).await;

    apply_stream_field_change(
        pool,
        stream_id,
        "field_correction",
        Some("resolution"),
        Some("1080p"),
        None,
    )
    .await;
    apply_stream_field_change(
        pool,
        stream_id,
        "field_correction",
        Some("source"),
        Some("WEB-DL"),
        None,
    )
    .await;
    apply_stream_field_change(
        pool,
        stream_id,
        "field_correction",
        Some("languages"),
        Some(r#"["English","Tamil"]"#),
        None,
    )
    .await;

    let (resolution, source): (Option<String>, Option<String>) =
        sqlx::query_as("SELECT resolution, source FROM stream WHERE id = $1")
            .bind(stream_id)
            .fetch_one(pool)
            .await
            .expect("fetch stream fields");

    assert_eq!(resolution.as_deref(), Some("1080p"));
    assert_eq!(source.as_deref(), Some("WEB-DL"));

    let lang_count: i64 = sqlx::query_scalar(
        r#"SELECT COUNT(*) FROM stream_language_link sll
           JOIN language l ON l.id = sll.language_id
           WHERE sll.stream_id = $1 AND l.name IN ('English', 'Tamil')"#,
    )
    .bind(stream_id)
    .fetch_one(pool)
    .await
    .expect("count languages");

    assert_eq!(lang_count, 2);

    sqlx::query("DELETE FROM stream_language_link WHERE stream_id = $1")
        .bind(stream_id)
        .execute(pool)
        .await
        .ok();
    sqlx::query("DELETE FROM stream WHERE id = $1")
        .bind(stream_id)
        .execute(pool)
        .await
        .ok();
    sqlx::query("DELETE FROM users WHERE id = $1")
        .bind(owner_id)
        .execute(pool)
        .await
        .ok();
}

/// Clearing episode_link fields via approved suggestions must NULL the file_media_link columns.
#[tokio::test]
async fn stream_suggestion_episode_link_clear_applies() {
    use mediafusion_api::db::{LinkSource, MediaType};
    use mediafusion_api::routes::content::stream_suggestions::apply_stream_field_change;

    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;

    let suffix = Uuid::new_v4().simple().to_string();
    let media_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO media (type, title, adult, is_blocked, is_public, is_user_created,
                              total_streams, nudity_status, year, created_at)
           VALUES ($1, $2, false, false, true, false, 0, 'UNKNOWN', 2020, NOW())
           RETURNING id"#,
    )
    .bind(MediaType::Series)
    .bind(format!("test_episode_link_{suffix}"))
    .fetch_one(pool)
    .await
    .expect("insert media");

    let stream_id: i32 = sqlx::query_scalar::<_, i32>(
        r#"INSERT INTO stream (
               stream_type, name, source, is_active, is_blocked, is_public, playback_count,
               is_remastered, is_upscaled, is_proper, is_repack, is_extended,
               is_complete, is_dubbed, is_subbed, created_at
           ) VALUES ('TORRENT', 'episode link test', 'test', true, false, true, 0,
                     false, false, false, false, false, false, false, false, NOW())
           RETURNING id"#,
    )
    .fetch_one(pool)
    .await
    .expect("insert stream");

    let info_hash = format!("bb{:038x}", stream_id);
    sqlx::query(
        r#"INSERT INTO torrent_stream (stream_id, info_hash, total_size, torrent_type, file_count, created_at)
           VALUES ($1, $2, 1000, 'PUBLIC', 1, NOW())"#,
    )
    .bind(stream_id)
    .bind(&info_hash)
    .execute(pool)
    .await
    .expect("insert torrent_stream");

    let file_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO stream_file (stream_id, file_index, filename, file_type, is_archive)
           VALUES ($1, 0, 'episode.mkv', 'VIDEO', false)
           RETURNING id"#,
    )
    .bind(stream_id)
    .fetch_one(pool)
    .await
    .expect("insert stream_file");

    sqlx::query(
        r#"INSERT INTO file_media_link (
               file_id, media_id, season_number, episode_number, episode_end,
               is_primary, confidence, link_source, created_at
           ) VALUES ($1, $2, 1, 2, 3, true, 1.0, $3, NOW())"#,
    )
    .bind(file_id)
    .bind(media_id)
    .bind(LinkSource::User)
    .execute(pool)
    .await
    .expect("insert file_media_link");

    apply_stream_field_change(
        pool,
        stream_id,
        "field_correction",
        Some(&format!("episode_link:{file_id}:season_number")),
        Some(""),
        None,
    )
    .await;
    apply_stream_field_change(
        pool,
        stream_id,
        "field_correction",
        Some(&format!("episode_link:{file_id}:episode_number")),
        Some(""),
        None,
    )
    .await;

    let (season, episode, episode_end): (Option<i32>, Option<i32>, Option<i32>) = sqlx::query_as(
        "SELECT season_number, episode_number, episode_end FROM file_media_link WHERE file_id = $1",
    )
    .bind(file_id)
    .fetch_one(pool)
    .await
    .expect("fetch file_media_link");

    assert_eq!(season, None);
    assert_eq!(episode, None);
    assert_eq!(episode_end, Some(3));

    sqlx::query("DELETE FROM file_media_link WHERE file_id = $1")
        .bind(file_id)
        .execute(pool)
        .await
        .ok();
    sqlx::query("DELETE FROM stream_file WHERE id = $1")
        .bind(file_id)
        .execute(pool)
        .await
        .ok();
    sqlx::query("DELETE FROM torrent_stream WHERE stream_id = $1")
        .bind(stream_id)
        .execute(pool)
        .await
        .ok();
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

#[tokio::test]
async fn stream_suggestion_episode_link_clear_all_applies() {
    use mediafusion_api::db::{LinkSource, MediaType};
    use mediafusion_api::routes::content::stream_suggestions::apply_stream_field_change;

    let _db = common::lock_db_tests().await;
    let pool = common::test_pool().await;

    let suffix = Uuid::new_v4().simple().to_string();
    let media_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO media (type, title, adult, is_blocked, is_public, is_user_created,
                              total_streams, nudity_status, year, created_at)
           VALUES ($1, $2, false, false, true, false, 0, 'UNKNOWN', 2020, NOW())
           RETURNING id"#,
    )
    .bind(MediaType::Series)
    .bind(format!("test_episode_clear_{suffix}"))
    .fetch_one(pool)
    .await
    .expect("insert media");

    let stream_id: i32 = sqlx::query_scalar::<_, i32>(
        r#"INSERT INTO stream (
               stream_type, name, source, is_active, is_blocked, is_public, playback_count,
               is_remastered, is_upscaled, is_proper, is_repack, is_extended,
               is_complete, is_dubbed, is_subbed, created_at
           ) VALUES ('TORRENT', 'episode clear test', 'test', true, false, true, 0,
                     false, false, false, false, false, false, false, false, NOW())
           RETURNING id"#,
    )
    .fetch_one(pool)
    .await
    .expect("insert stream");

    let file_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO stream_file (stream_id, file_index, filename, file_type, is_archive)
           VALUES ($1, 0, 'episode.mkv', 'VIDEO', false)
           RETURNING id"#,
    )
    .bind(stream_id)
    .fetch_one(pool)
    .await
    .expect("insert stream_file");

    sqlx::query(
        r#"INSERT INTO file_media_link (
               file_id, media_id, season_number, episode_number, episode_end,
               is_primary, confidence, link_source, created_at
           ) VALUES ($1, $2, 1, 2, 3, true, 1.0, $3, NOW())"#,
    )
    .bind(file_id)
    .bind(media_id)
    .bind(LinkSource::User)
    .execute(pool)
    .await
    .expect("insert file_media_link");

    apply_stream_field_change(
        pool,
        stream_id,
        "field_correction",
        Some(&format!("episode_link:{file_id}:clear")),
        Some(""),
        None,
    )
    .await;

    let (season, episode, episode_end): (Option<i32>, Option<i32>, Option<i32>) = sqlx::query_as(
        "SELECT season_number, episode_number, episode_end FROM file_media_link WHERE file_id = $1",
    )
    .bind(file_id)
    .fetch_one(pool)
    .await
    .expect("fetch file_media_link");

    assert_eq!(season, None);
    assert_eq!(episode, None);
    assert_eq!(episode_end, None);

    sqlx::query("DELETE FROM file_media_link WHERE file_id = $1")
        .bind(file_id)
        .execute(pool)
        .await
        .ok();
    sqlx::query("DELETE FROM stream_file WHERE id = $1")
        .bind(file_id)
        .execute(pool)
        .await
        .ok();
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
