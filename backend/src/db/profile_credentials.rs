//! Idempotent startup backfill; encryption requires the application secret key.
use fred::{clients::Client, prelude::KeysInterface};
use sqlx::PgPool;

use super::types::{ProfileCredentialRow, ProfileId};

pub async fn protect_qbittorrent_credentials(
    pool: &PgPool,
    redis: &Client,
    key: &[u8; 32],
) -> Result<usize, Box<dyn std::error::Error + Send + Sync>> {
    let mut last_id = ProfileId(0);
    let mut changed = 0;
    loop {
        let mut tx = pool.begin().await?;
        // Row locks keep a simultaneous profile edit or token refresh from being lost.
        let rows = sqlx::query_as::<_, ProfileCredentialRow>(
            "SELECT id, uuid, config, encrypted_secrets FROM user_profiles \
             WHERE id > $1 ORDER BY id LIMIT 200 FOR UPDATE",
        )
        .bind(last_id)
        .fetch_all(&mut *tx)
        .await?;
        if rows.is_empty() {
            break;
        }
        let mut cache_keys = Vec::new();
        for row in rows {
            last_id = row.id;
            if let Some((config, encrypted)) = crate::crypto::qbittorrent_credentials::reprotect(
                &row.config,
                row.encrypted_secrets.as_deref(),
                key,
            )
            .map_err(|message| format!("Profile {}: {message}", row.id))?
            {
                sqlx::query(
                    "UPDATE user_profiles SET config = $1, encrypted_secrets = $2 WHERE id = $3",
                )
                .bind(config)
                .bind(encrypted)
                .bind(row.id)
                .execute(&mut *tx)
                .await?;
                changed += 1;
            }
            cache_keys.push(format!("user_profile:{}", row.uuid));
        }
        tx.commit().await?;
        // Invalidate even unchanged rows: a previous run might have committed
        // the DB batch before crashing or failing to invalidate Redis.
        redis.del::<i64, _>(cache_keys).await?;
    }
    if changed > 0 {
        tracing::info!(
            profiles = changed,
            "encrypted stored qBittorrent/WebDAV credentials"
        );
    }
    Ok(changed)
}
