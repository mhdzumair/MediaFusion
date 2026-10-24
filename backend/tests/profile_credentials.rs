//! Run with TEST_DATABASE_URL and TEST_REDIS_URL against local test services.
use fred::prelude::*;
use mediafusion_api::{cache, crypto, db};
use serde_json::json;
use sqlx::postgres::PgPoolOptions;

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL and TEST_REDIS_URL"]
async fn credential_backfill_encrypts_existing_profiles_and_invalidates_stale_caches() {
    let database_url = std::env::var("TEST_DATABASE_URL").expect("set TEST_DATABASE_URL");
    let admin = PgPoolOptions::new()
        .max_connections(1)
        .connect(&database_url)
        .await
        .unwrap();
    let schema = format!("test_profile_credentials_{}", uuid::Uuid::new_v4().simple());
    // The identifier consists only of a constant prefix and generated UUID hex.
    sqlx::QueryBuilder::<sqlx::Postgres>::new("CREATE SCHEMA ")
        .push(&schema)
        .build()
        .execute(&admin)
        .await
        .unwrap();
    let search_path = schema.clone();
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .after_connect(move |connection, _| {
            let search_path = search_path.clone();
            Box::pin(async move {
                sqlx::query("SELECT set_config('search_path', $1, false)")
                    .bind(search_path)
                    .execute(connection)
                    .await?;
                Ok(())
            })
        })
        .connect(&database_url)
        .await
        .unwrap();
    sqlx::query("CREATE TABLE user_profiles (id integer PRIMARY KEY, uuid text NOT NULL, config jsonb NOT NULL, encrypted_secrets text)")
        .execute(&pool).await.unwrap();
    let redis = cache::client::build(&std::env::var("TEST_REDIS_URL").expect("set TEST_REDIS_URL"))
        .await
        .unwrap();
    let key = [42; 32];
    let uuid = uuid::Uuid::new_v4().to_string();
    let config = json!({"sps":[
        {"sv":"qbittorrent","qbc":{"wpw":"synthetic-dav-secret","qur":"http://qb"}},
        {"sv":"realdebrid"}
    ]});
    let existing = json!({"sps":[
        {"_index":1,"tk":"preserve-debrid-token"},
        {"_index":0,"qbc":{"qpw":"preserve-qb-secret"}}
    ], "unknown":{"keep":true}});
    let encrypted = crypto::profile::encrypt_secrets(&existing, &key).unwrap();
    sqlx::query("INSERT INTO user_profiles VALUES ($1,$2,$3,$4)")
        .bind(1i32)
        .bind(&uuid)
        .bind(&config)
        .bind(&encrypted)
        .execute(&pool)
        .await
        .unwrap();
    let cache_key = format!("user_profile:{uuid}");
    redis
        .set::<(), _, _>(
            &cache_key,
            json!({"config":config,"encrypted_secrets":encrypted}).to_string(),
            Some(Expiration::EX(60)),
            None,
            false,
        )
        .await
        .unwrap();
    assert_eq!(
        db::profile_credentials::protect_qbittorrent_credentials(&pool, &redis, &key)
            .await
            .unwrap(),
        1
    );
    let row = sqlx::query_as::<_, db::types::ProfileCredentialRow>(
        "SELECT * FROM user_profiles WHERE id = 1",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(row.config["sps"][0]["qbc"].get("wpw").is_none());
    let secrets = crypto::profile::decrypt_secrets(&row.encrypted_secrets.unwrap(), &key);
    assert_eq!(secrets["unknown"], existing["unknown"]);
    let mut full = row.config;
    crypto::profile::merge_secrets(&mut full, &secrets);
    assert_eq!(full["sps"][0]["qbc"]["wpw"], "synthetic-dav-secret");
    assert_eq!(full["sps"][0]["qbc"]["qpw"], "preserve-qb-secret");
    assert_eq!(full["sps"][1]["tk"], "preserve-debrid-token");
    assert!(!redis.exists::<bool, _>(&cache_key).await.unwrap());
    // Simulate a previous run committing the DB update but failing cache cleanup.
    redis
        .set::<(), _, _>(
            &cache_key,
            "stale-plaintext-cache",
            Some(Expiration::EX(60)),
            None,
            false,
        )
        .await
        .unwrap();
    assert_eq!(
        db::profile_credentials::protect_qbittorrent_credentials(&pool, &redis, &key)
            .await
            .unwrap(),
        0
    );
    assert!(!redis.exists::<bool, _>(&cache_key).await.unwrap());
    sqlx::query("INSERT INTO user_profiles VALUES ($1,$2,$3,$4)")
        .bind(2i32)
        .bind(uuid::Uuid::new_v4().to_string())
        .bind(&config)
        .bind("unreadable")
        .execute(&pool)
        .await
        .unwrap();
    assert!(
        db::profile_credentials::protect_qbittorrent_credentials(&pool, &redis, &key)
            .await
            .is_err()
    );
    let row = sqlx::query_as::<_, db::types::ProfileCredentialRow>(
        "SELECT * FROM user_profiles WHERE id = 2",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        row.config, config,
        "unreadable secrets must not be overwritten"
    );
    assert_eq!(row.encrypted_secrets.as_deref(), Some("unreadable"));
    pool.close().await;
    sqlx::QueryBuilder::<sqlx::Postgres>::new("DROP SCHEMA ")
        .push(&schema)
        .push(" CASCADE")
        .build()
        .execute(&admin)
        .await
        .unwrap();
    admin.close().await;
}
