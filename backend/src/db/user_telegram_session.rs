//! Per-user encrypted Telegram MTProto session storage.

use chrono::{DateTime, Utc};
use sqlx::PgPool;

use super::types::UserId;

pub struct UserTelegramSessionRow {
    pub encrypted_session: String,
    pub telegram_account_id: i64,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub last_used_at: Option<DateTime<Utc>>,
}

type SessionTuple = (
    String,
    i64,
    DateTime<Utc>,
    DateTime<Utc>,
    Option<DateTime<Utc>>,
);

pub async fn get_session(pool: &PgPool, user_id: UserId) -> Option<UserTelegramSessionRow> {
    sqlx::query_as::<_, SessionTuple>(
        r#"SELECT encrypted_session, telegram_account_id, created_at, updated_at, last_used_at
           FROM user_telegram_sessions WHERE user_id = $1"#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
    .map(
        |(encrypted_session, telegram_account_id, created_at, updated_at, last_used_at)| {
            UserTelegramSessionRow {
                encrypted_session,
                telegram_account_id,
                created_at,
                updated_at,
                last_used_at,
            }
        },
    )
}

pub async fn upsert_session(
    pool: &PgPool,
    user_id: UserId,
    encrypted_session: &str,
    telegram_account_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"INSERT INTO user_telegram_sessions (user_id, encrypted_session, telegram_account_id, updated_at)
           VALUES ($1, $2, $3, NOW())
           ON CONFLICT (user_id) DO UPDATE SET
             encrypted_session = EXCLUDED.encrypted_session,
             telegram_account_id = EXCLUDED.telegram_account_id,
             updated_at = NOW(),
             last_used_at = NULL"#,
    )
    .bind(user_id)
    .bind(encrypted_session)
    .bind(telegram_account_id)
    .execute(pool)
    .await?;
    Ok(())
}

pub async fn delete_session(pool: &PgPool, user_id: UserId) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("DELETE FROM user_telegram_sessions WHERE user_id = $1")
        .bind(user_id)
        .execute(pool)
        .await?;
    Ok(result.rows_affected() > 0)
}

pub async fn touch_last_used(pool: &PgPool, user_id: UserId) {
    let _ =
        sqlx::query("UPDATE user_telegram_sessions SET last_used_at = NOW() WHERE user_id = $1")
            .bind(user_id)
            .execute(pool)
            .await;
}

pub async fn has_session(pool: &PgPool, user_id: UserId) -> bool {
    sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM user_telegram_sessions WHERE user_id = $1)",
    )
    .bind(user_id)
    .fetch_one(pool)
    .await
    .unwrap_or(false)
}

pub struct UserTelegramScrapeTarget {
    pub user_id: UserId,
    pub channels: Vec<String>,
}

/// Users with stored sessions and enabled scraping channels in their default profile.
pub async fn list_scrape_targets(pool: &PgPool) -> Vec<UserTelegramScrapeTarget> {
    let rows: Vec<(i32, serde_json::Value)> = match sqlx::query_as(
        r#"SELECT u.id, up.config->'tgc' AS tgc
           FROM user_telegram_sessions uts
           JOIN users u ON u.id = uts.user_id
           JOIN user_profiles up ON up.user_id = u.id AND up.is_default = true"#,
    )
    .fetch_all(pool)
    .await
    {
        Ok(rows) => rows,
        Err(e) => {
            tracing::warn!("list_scrape_targets: {e}");
            return vec![];
        }
    };

    rows.into_iter()
        .filter_map(|(user_id, tgc)| {
            let channels = super::telegram_channels::channels_from_tgc(&tgc);
            if channels.is_empty() {
                return None;
            }
            Some(UserTelegramScrapeTarget {
                user_id: UserId(user_id),
                channels,
            })
        })
        .collect()
}
