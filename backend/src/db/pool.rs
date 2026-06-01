use sqlx::{postgres::PgPoolOptions, PgPool};
use std::time::Duration;

pub async fn build(uri: &str, max_connections: u32) -> Result<PgPool, sqlx::Error> {
    PgPoolOptions::new()
        .max_connections(max_connections)
        .acquire_timeout(Duration::from_secs(5))
        .connect(uri)
        .await
}
