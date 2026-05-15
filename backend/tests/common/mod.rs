use sqlx::PgPool;

#[allow(dead_code)]
pub async fn test_pool() -> PgPool {
    let url = std::env::var("TEST_DATABASE_URL").unwrap_or_else(|_| {
        "postgresql://mediafusion:mediafusion@127.0.0.1:5432/mediafusion".into()
    });
    PgPool::connect(&url).await.expect("test DB connect failed")
}
