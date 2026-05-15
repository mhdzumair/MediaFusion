use chrono::{DateTime, Utc};
use sqlx::PgPool;

/// Minimal TelegramStream data needed for playback.
pub struct TelegramStreamRow {
    pub id: i64,
    pub file_id: Option<String>,
    pub file_unique_id: Option<String>,
    pub document_id: Option<i64>,
    pub file_name: Option<String>,
    pub size: Option<i64>,
    pub stream_name: Option<String>,
}

/// TelegramUserForward row — maps (telegram_stream_id, user_id) to the forwarded copy.
pub struct TelegramUserForwardRow {
    pub id: i64,
    pub telegram_stream_id: i64,
    pub user_id: i64,
    pub telegram_user_id: i64,
    pub forwarded_chat_id: String,
    pub forwarded_message_id: i64,
    pub created_at: DateTime<Utc>,
}

type TgStreamTuple = (
    i64,
    Option<String>,
    Option<String>,
    Option<i64>,
    Option<String>,
    Option<i64>,
    Option<String>,
);
type TgForwardTuple = (i64, i64, i64, i64, String, i64, DateTime<Utc>);

fn tuple_to_stream_row(r: TgStreamTuple) -> TelegramStreamRow {
    TelegramStreamRow {
        id: r.0,
        file_id: r.1,
        file_unique_id: r.2,
        document_id: r.3,
        file_name: r.4,
        size: r.5,
        stream_name: r.6,
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
               ts.file_name, ts.size, st.name
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
               ts.file_name, ts.size, st.name
        FROM telegram_stream ts
        JOIN stream st ON st.id = ts.stream_id
        WHERE ts.id = $1
        LIMIT 1
        "#,
    )
    .bind(telegram_stream_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)
    .map(tuple_to_stream_row)
}

/// Look up the forwarded copy for (telegram_stream_id, user_id).
pub async fn get_telegram_user_forward(
    pool: &PgPool,
    telegram_stream_id: i64,
    user_id: i64,
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
    .bind(telegram_stream_id)
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
    user_id: i64,
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
    .bind(telegram_stream_id)
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
    .bind(telegram_stream_id)
    .bind(user_id)
    .fetch_one(pool)
    .await
    .map(tuple_to_forward_row)
}

/// Delete a TelegramUserForward row (used when refreshing stale forwards).
pub async fn delete_telegram_user_forward(pool: &PgPool, telegram_stream_id: i64, user_id: i64) {
    let _ = sqlx::query(
        "DELETE FROM telegram_user_forward WHERE telegram_stream_id = $1 AND user_id = $2",
    )
    .bind(telegram_stream_id)
    .bind(user_id)
    .execute(pool)
    .await;
}

/// Get the telegram_user_id for a given MediaFusion user_id.
pub async fn get_user_telegram_id(pool: &PgPool, user_id: i64) -> Option<i64> {
    sqlx::query_scalar::<_, Option<i64>>("SELECT telegram_user_id FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
        .flatten()
}
