use sqlx::{postgres::PgPoolOptions, PgPool};
use std::time::Duration;

pub async fn build(uri: &str) -> Result<PgPool, sqlx::Error> {
    PgPoolOptions::new()
        .max_connections(50)
        .acquire_timeout(Duration::from_secs(5))
        .connect(uri)
        .await
}
