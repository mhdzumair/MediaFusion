use chrono::{DateTime, Utc};
use sqlx::PgPool;

use super::types::UserId;

/// Minimal TelegramStream data needed for playback.
pub struct TelegramStreamRow {
    pub id: i32,
    pub file_id: Option<String>,
    pub file_unique_id: Option<String>,
    pub document_id: Option<i64>,
    pub file_name: Option<String>,
    pub size: Option<i64>,
    pub stream_name: Option<String>,
    pub backup_chat_id: Option<String>,
    pub backup_message_id: Option<i32>,
}

/// TelegramUserForward row — maps (telegram_stream_id, user_id) to the forwarded copy.
pub struct TelegramUserForwardRow {
    pub id: i32,
    pub telegram_stream_id: i32,
    pub user_id: UserId,
    pub telegram_user_id: i64,
    pub forwarded_chat_id: String,
    pub forwarded_message_id: i64,
    pub created_at: DateTime<Utc>,
}

type TgStreamTuple = (
    i32,
    Option<String>,
    Option<String>,
    Option<i64>,
    Option<String>,
    Option<i64>,
    Option<String>,
    Option<String>,
    Option<i32>,
);
type TgForwardTuple = (i32, i32, UserId, i64, String, i64, DateTime<Utc>);

fn tuple_to_stream_row(r: TgStreamTuple) -> TelegramStreamRow {
    TelegramStreamRow {
        id: r.0,
        file_id: r.1,
        file_unique_id: r.2,
        document_id: r.3,
        file_name: r.4,
        size: r.5,
        stream_name: r.6,
        backup_chat_id: r.7,
        backup_message_id: r.8,
    }
}

fn tuple_to_forward_row(r: TgForwardTuple) -> TelegramUserForwardRow {
    TelegramUserForwardRow {
        id: r.0,
        telegram_stream_id: r.1,
        user_id: r.2,
        telegram_user_id: r.3,
        forwarded_chat_id: r.4,
        forwarded_message_id: r.5,
        created_at: r.6,
    }
}

/// Fetch a TelegramStream by (chat_id, message_id).
pub async fn fetch_telegram_stream_by_chat_message(
    pool: &PgPool,
    chat_id: &str,
    message_id: i64,
) -> Option<TelegramStreamRow> {
    sqlx::query_as::<_, TgStreamTuple>(
        r#"
        SELECT ts.id, ts.file_id, ts.file_unique_id, ts.document_id,
               ts.file_name, ts.size, st.name,
               ts.backup_chat_id, ts.backup_message_id
        FROM telegram_stream ts
        JOIN stream st ON st.id = ts.stream_id
        WHERE ts.chat_id = $1 AND ts.message_id = $2
        LIMIT 1
        "#,
    )
    .bind(chat_id)
    .bind(message_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)
    .map(tuple_to_stream_row)
}

/// Fetch a TelegramStream by its primary key.
pub async fn fetch_telegram_stream_by_id(
    pool: &PgPool,
    telegram_stream_id: i64,
) -> Option<TelegramStreamRow> {
    sqlx::query_as::<_, TgStreamTuple>(
        r#"
        SELECT ts.id, ts.file_id, ts.file_unique_id, ts.document_id,
               ts.file_name, ts.size, st.name,
               ts.backup_chat_id, ts.backup_message_id
        FROM telegram_stream ts
        JOIN stream st ON st.id = ts.stream_id
        WHERE ts.id = $1
        LIMIT 1
        "#,
    )
    .bind(telegram_stream_id as i32)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)
    .map(tuple_to_stream_row)
}

/// Look up the forwarded copy for (telegram_stream_id, user_id).
pub async fn get_telegram_user_forward(
    pool: &PgPool,
    telegram_stream_id: i64,
    user_id: UserId,
) -> Option<TelegramUserForwardRow> {
    sqlx::query_as::<_, TgForwardTuple>(
        r#"
        SELECT id, telegram_stream_id, user_id, telegram_user_id,
               forwarded_chat_id, forwarded_message_id, created_at
        FROM telegram_user_forward
        WHERE telegram_stream_id = $1 AND user_id = $2
        LIMIT 1
        "#,
    )
    .bind(telegram_stream_id as i32)
    .bind(user_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)
    .map(tuple_to_forward_row)
}

/// Insert a new TelegramUserForward row. Returns the inserted row.
pub async fn create_telegram_user_forward(
    pool: &PgPool,
    telegram_stream_id: i64,
    user_id: UserId,
    telegram_user_id: i64,
    forwarded_chat_id: &str,
    forwarded_message_id: i64,
) -> Result<TelegramUserForwardRow, sqlx::Error> {
    // Try insert; if conflict (race), fall back to SELECT.
    let inserted: Option<TgForwardTuple> = sqlx::query_as(
        r#"
        INSERT INTO telegram_user_forward
            (telegram_stream_id, user_id, telegram_user_id, forwarded_chat_id, forwarded_message_id)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (telegram_stream_id, user_id) DO NOTHING
        RETURNING id, telegram_stream_id, user_id, telegram_user_id,
                  forwarded_chat_id, forwarded_message_id, created_at
        "#,
    )
    .bind(telegram_stream_id as i32)
    .bind(user_id)
    .bind(telegram_user_id)
    .bind(forwarded_chat_id)
    .bind(forwarded_message_id)
    .fetch_optional(pool)
    .await?;

    if let Some(row) = inserted {
        return Ok(tuple_to_forward_row(row));
    }

    // Race: another request inserted first — fetch the existing row.
    sqlx::query_as::<_, TgForwardTuple>(
        r#"
        SELECT id, telegram_stream_id, user_id, telegram_user_id,
               forwarded_chat_id, forwarded_message_id, created_at
        FROM telegram_user_forward
        WHERE telegram_stream_id = $1 AND user_id = $2
        "#,
    )
    .bind(telegram_stream_id as i32)
    .bind(user_id)
    .fetch_one(pool)
    .await
    .map(tuple_to_forward_row)
}

pub async fn update_telegram_stream_file_id(
    pool: &PgPool,
    telegram_stream_id: i32,
    file_id: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE telegram_stream SET file_id = $1 WHERE id = $2")
        .bind(file_id)
        .bind(telegram_stream_id)
        .execute(pool)
        .await?;
    Ok(())
}

/// Delete a TelegramUserForward row (used when refreshing stale forwards).
pub async fn delete_telegram_user_forward(pool: &PgPool, telegram_stream_id: i64, user_id: UserId) {
    let _ = sqlx::query(
        "DELETE FROM telegram_user_forward WHERE telegram_stream_id = $1 AND user_id = $2",
    )
    .bind(telegram_stream_id as i32)
    .bind(user_id)
    .execute(pool)
    .await;
}

/// Get the telegram_user_id for a given MediaFusion user_id.
pub async fn get_user_telegram_id(pool: &PgPool, user_id: UserId) -> Option<i64> {
    // Column is character varying; read as String then parse to i64.
    let val: Option<String> =
        sqlx::query_scalar::<_, Option<String>>("SELECT telegram_user_id FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten()
            .flatten();
    val.and_then(|s| s.parse().ok())
}

/// Look up MediaFusion user by linked Telegram user ID (for /status and bot imports).
pub async fn get_user_by_telegram_id(
    pool: &PgPool,
    telegram_user_id: i64,
) -> Option<(UserId, String)> {
    // Column is character varying; bind as String to avoid operator type mismatch.
    let row: Option<(UserId, String)> = sqlx::query_as(
        "SELECT id, COALESCE(NULLIF(username, ''), NULLIF(email, ''), 'User #' || id::text) \
         FROM users WHERE telegram_user_id = $1 LIMIT 1",
    )
    .bind(telegram_user_id.to_string())
    .fetch_optional(pool)
    .await
    .ok()?;
    row
}

/// Resolve MediaFusion user_id from Telegram user_id via DB or Redis mapping cache.
pub async fn resolve_mediafusion_user_id(
    pool: &PgPool,
    redis: &fred::clients::Client,
    telegram_user_id: i64,
) -> Option<UserId> {
    use fred::prelude::KeysInterface;

    if let Some((uid, _)) = get_user_by_telegram_id(pool, telegram_user_id).await {
        return Some(uid);
    }
    // Fall back to the short-lived Redis mapping cache written at link time.
    let key = crate::bot::user_mapping_key(telegram_user_id);
    let cached: Option<String> = redis.get(&key).await.ok()?;
    cached.and_then(|s| s.parse::<i32>().ok()).map(UserId)
}

#[derive(Debug, Default, Clone)]
pub struct TelegramBackupStats {
    pub total_streams: i64,
    pub with_file_id: i64,
    pub without_file_id: i64,
    pub with_backup: i64,
    pub without_backup: i64,
    pub with_file_unique_id: i64,
}

pub async fn fetch_telegram_backup_stats(pool: &PgPool) -> TelegramBackupStats {
    let row: Option<(i64, i64, i64, i64, i64, i64)> = sqlx::query_as(
        r#"
        SELECT
            COUNT(*)::bigint,
            COUNT(*) FILTER (
                WHERE file_id IS NOT NULL AND file_id <> ''
            )::bigint,
            COUNT(*) FILTER (
                WHERE file_id IS NULL OR file_id = ''
            )::bigint,
            COUNT(*) FILTER (
                WHERE backup_chat_id IS NOT NULL AND backup_chat_id <> ''
                  AND backup_message_id IS NOT NULL
            )::bigint,
            COUNT(*) FILTER (
                WHERE backup_chat_id IS NULL OR backup_chat_id = ''
                  OR backup_message_id IS NULL
            )::bigint,
            COUNT(*) FILTER (
                WHERE file_unique_id IS NOT NULL AND file_unique_id <> ''
            )::bigint
        FROM telegram_stream
        "#,
    )
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    row.map(
        |(
            total,
            with_file_id,
            without_file_id,
            with_backup,
            without_backup,
            with_file_unique_id,
        )| {
            TelegramBackupStats {
                total_streams: total,
                with_file_id,
                without_file_id,
                with_backup,
                without_backup,
                with_file_unique_id,
            }
        },
    )
    .unwrap_or_default()
}

#[derive(Debug, Clone)]
pub struct TelegramStreamBackupRow {
    pub id: i32,
    pub chat_id: String,
    pub message_id: i32,
    pub file_name: String,
    pub file_unique_id: Option<String>,
    pub document_id: Option<i64>,
    pub stream_name: String,
    pub backup_chat_id: Option<String>,
    pub backup_message_id: Option<i32>,
    pub file_id: Option<String>,
}

type BackupRowTuple = (
    i32,
    String,
    i32,
    String,
    Option<String>,
    Option<i64>,
    String,
    Option<String>,
    Option<i32>,
    Option<String>,
);

fn tuple_to_backup_row(r: BackupRowTuple) -> TelegramStreamBackupRow {
    TelegramStreamBackupRow {
        id: r.0,
        chat_id: r.1,
        message_id: r.2,
        file_name: r.3,
        file_unique_id: r.4,
        document_id: r.5,
        stream_name: r.6,
        backup_chat_id: r.7,
        backup_message_id: r.8,
        file_id: r.9,
    }
}

pub async fn list_streams_for_backup_store(
    pool: &PgPool,
    after_id: i32,
    limit: i64,
    only_missing: bool,
) -> Vec<TelegramStreamBackupRow> {
    let rows: Vec<BackupRowTuple> = sqlx::query_as(
        r#"
        SELECT ts.id, ts.chat_id, ts.message_id, ts.file_name, ts.file_unique_id,
               ts.document_id, st.name, ts.backup_chat_id, ts.backup_message_id, ts.file_id
        FROM telegram_stream ts
        JOIN stream st ON st.id = ts.stream_id
        WHERE ts.id > $1
          AND ($2 = false OR ts.backup_chat_id IS NULL OR ts.backup_chat_id = ''
               OR ts.backup_message_id IS NULL)
        ORDER BY ts.id
        LIMIT $3
        "#,
    )
    .bind(after_id)
    .bind(!only_missing)
    .bind(limit)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    rows.into_iter().map(tuple_to_backup_row).collect()
}

pub async fn find_stream_for_restore(
    pool: &PgPool,
    file_unique_id: Option<&str>,
    file_name: Option<&str>,
) -> Option<TelegramStreamBackupRow> {
    if let Some(unique) = file_unique_id.filter(|s| !s.is_empty()) {
        let row: Option<BackupRowTuple> = sqlx::query_as(
            r#"
            SELECT ts.id, ts.chat_id, ts.message_id, ts.file_name, ts.file_unique_id,
                   ts.document_id, st.name, ts.backup_chat_id, ts.backup_message_id, ts.file_id
            FROM telegram_stream ts
            JOIN stream st ON st.id = ts.stream_id
            WHERE ts.file_unique_id = $1
            LIMIT 1
            "#,
        )
        .bind(unique)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);
        if row.is_some() {
            return row.map(tuple_to_backup_row);
        }
    }

    let name = file_name.filter(|s| !s.is_empty())?;
    sqlx::query_as::<_, BackupRowTuple>(
        r#"
        SELECT ts.id, ts.chat_id, ts.message_id, ts.file_name, ts.file_unique_id,
               ts.document_id, st.name, ts.backup_chat_id, ts.backup_message_id, ts.file_id
        FROM telegram_stream ts
        JOIN stream st ON st.id = ts.stream_id
        WHERE ts.file_name = $1
        ORDER BY ts.id DESC
        LIMIT 1
        "#,
    )
    .bind(name)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)
    .map(tuple_to_backup_row)
}

pub async fn update_telegram_stream_backup(
    pool: &PgPool,
    telegram_stream_id: i32,
    backup_chat_id: &str,
    backup_message_id: i32,
    file_id: Option<&str>,
) -> Result<(), sqlx::Error> {
    if let Some(fid) = file_id.filter(|s| !s.is_empty()) {
        sqlx::query(
            r#"
            UPDATE telegram_stream
            SET backup_chat_id = $1, backup_message_id = $2, file_id = $3
            WHERE id = $4
            "#,
        )
        .bind(backup_chat_id)
        .bind(backup_message_id)
        .bind(fid)
        .bind(telegram_stream_id)
        .execute(pool)
        .await?;
    } else {
        sqlx::query(
            r#"
            UPDATE telegram_stream
            SET backup_chat_id = $1, backup_message_id = $2
            WHERE id = $3
            "#,
        )
        .bind(backup_chat_id)
        .bind(backup_message_id)
        .bind(telegram_stream_id)
        .execute(pool)
        .await?;
    }
    Ok(())
}
