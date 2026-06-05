/// Orphan detection and cleanup.
use std::collections::HashMap;
use std::sync::Arc;

use axum::{
    extract::{Query, State},
    http::HeaderMap,
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::Row;

use crate::state::AppState;

use super::{forbidden, validate_admin};

#[derive(Deserialize)]
pub struct OrphanCleanupQuery {
    pub dry_run: Option<bool>,
}

#[derive(Deserialize)]
pub struct OrphanCleanupBody {
    pub tables: Option<Vec<String>>,
}

/// GET /api/v1/admin/db/orphans
/// Returns record-level orphan detail for 5 categories.
pub async fn detect_orphans_combined(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let timeout = "SET LOCAL statement_timeout = '30s'";

    // 1. Streams with no stream_media_link
    let orphan_streams_task = async {
        let mut tx = state.pool_ro.begin().await.ok()?;
        sqlx::query(timeout).execute(&mut *tx).await.ok()?;
        let rows = sqlx::query(
            "SELECT s.id::text, s.created_at::text \
             FROM stream s \
             WHERE NOT EXISTS (SELECT 1 FROM stream_media_link sml WHERE sml.stream_id = s.id) \
             LIMIT 1000",
        )
        .fetch_all(&mut *tx)
        .await
        .ok()?;
        Some(rows)
    };

    // 2. torrent_stream with no parent stream
    let orphan_torrent_task = async {
        let mut tx = state.pool_ro.begin().await.ok()?;
        sqlx::query(timeout).execute(&mut *tx).await.ok()?;
        let rows = sqlx::query(
            "SELECT ts.id::text, ts.created_at::text \
             FROM torrent_stream ts \
             WHERE NOT EXISTS (SELECT 1 FROM stream s WHERE s.id = ts.stream_id) \
             LIMIT 1000",
        )
        .fetch_all(&mut *tx)
        .await
        .ok()?;
        Some(rows)
    };

    // 3. stream_file with no parent stream
    let orphan_stream_file_task = async {
        let mut tx = state.pool_ro.begin().await.ok()?;
        sqlx::query(timeout).execute(&mut *tx).await.ok()?;
        let rows = sqlx::query(
            "SELECT sf.id::text, sf.created_at::text \
             FROM stream_file sf \
             WHERE NOT EXISTS (SELECT 1 FROM stream s WHERE s.id = sf.stream_id) \
             LIMIT 1000",
        )
        .fetch_all(&mut *tx)
        .await
        .ok()?;
        Some(rows)
    };

    // 4. media with no stream_media_link
    let orphan_media_task = async {
        let mut tx = state.pool_ro.begin().await.ok()?;
        sqlx::query(timeout).execute(&mut *tx).await.ok()?;
        let rows = sqlx::query(
            "SELECT m.id::text, m.created_at::text \
             FROM media m \
             WHERE m.type IN ('movie','series') \
               AND NOT EXISTS (SELECT 1 FROM stream_media_link sml WHERE sml.meta_id = m.id) \
             LIMIT 1000",
        )
        .fetch_all(&mut *tx)
        .await
        .ok()?;
        Some(rows)
    };

    // 5. stream_media_link pointing to missing media
    let orphan_sml_task = async {
        let mut tx = state.pool_ro.begin().await.ok()?;
        sqlx::query(timeout).execute(&mut *tx).await.ok()?;
        let rows = sqlx::query(
            "SELECT sml.id::text, sml.created_at::text \
             FROM stream_media_link sml \
             WHERE NOT EXISTS (SELECT 1 FROM media m WHERE m.id = sml.meta_id) \
             LIMIT 1000",
        )
        .fetch_all(&mut *tx)
        .await
        .ok()?;
        Some(rows)
    };

    let (streams_rows, torrent_rows, sf_rows, media_rows, sml_rows) = tokio::join!(
        orphan_streams_task,
        orphan_torrent_task,
        orphan_stream_file_task,
        orphan_media_task,
        orphan_sml_task,
    );

    let mut orphans: Vec<Value> = Vec::new();
    let mut by_type: HashMap<String, i64> = HashMap::new();

    let add_records = |rows: Option<Vec<sqlx::postgres::PgRow>>,
                       table: &str,
                       reason: &str,
                       orphans: &mut Vec<Value>,
                       by_type: &mut HashMap<String, i64>| {
        let records = rows.unwrap_or_default();
        let count = records.len() as i64;
        *by_type.entry(table.to_string()).or_insert(0) += count;
        for row in records {
            let id: String = row.try_get("id").unwrap_or_else(|_| "unknown".to_string());
            let created_at: Option<String> = row.try_get("created_at").ok();
            orphans.push(json!({
                "table": table,
                "id": id,
                "reason": reason,
                "created_at": created_at,
            }));
        }
    };

    add_records(
        streams_rows,
        "stream",
        "No stream_media_link referencing this stream",
        &mut orphans,
        &mut by_type,
    );
    add_records(
        torrent_rows,
        "torrent_stream",
        "No parent stream record",
        &mut orphans,
        &mut by_type,
    );
    add_records(
        sf_rows,
        "stream_file",
        "No parent stream record",
        &mut orphans,
        &mut by_type,
    );
    add_records(
        media_rows,
        "media",
        "No stream_media_link referencing this media",
        &mut orphans,
        &mut by_type,
    );
    add_records(
        sml_rows,
        "stream_media_link",
        "References non-existent media",
        &mut orphans,
        &mut by_type,
    );

    let total_count = orphans.len() as i64;

    Json(json!({
        "orphans": orphans,
        "total_count": total_count,
        "by_type": by_type,
    }))
    .into_response()
}

/// POST /api/v1/admin/db/orphans/cleanup
pub async fn cleanup_orphans(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(query): Query<OrphanCleanupQuery>,
    body: Option<Json<OrphanCleanupBody>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let dry_run = query.dry_run.unwrap_or(true);
    let default_tables = vec![
        "stream_file".to_string(),
        "torrent_stream".to_string(),
        "stream".to_string(),
        "stream_media_link".to_string(),
    ];
    let tables = body
        .and_then(|Json(b)| b.tables)
        .unwrap_or_else(|| default_tables.clone());

    let mut deleted: HashMap<String, i64> = HashMap::new();
    let mut would_delete: HashMap<String, i64> = HashMap::new();

    for table in &tables {
        match table.as_str() {
            "stream_file" => {
                let count: i64 = sqlx::query_scalar(
                    "SELECT COUNT(*) FROM stream_file sf \
                     WHERE NOT EXISTS (SELECT 1 FROM stream s WHERE s.id = sf.stream_id)",
                )
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);

                if dry_run {
                    would_delete.insert("stream_file".to_string(), count);
                } else {
                    match sqlx::query(
                        "DELETE FROM stream_file sf \
                         WHERE NOT EXISTS (SELECT 1 FROM stream s WHERE s.id = sf.stream_id)",
                    )
                    .execute(&state.pool)
                    .await
                    {
                        Ok(r) => {
                            deleted.insert("stream_file".to_string(), r.rows_affected() as i64);
                        }
                        Err(e) => {
                            tracing::error!("cleanup stream_file: {e}");
                        }
                    }
                }
            }
            "torrent_stream" => {
                let count: i64 = sqlx::query_scalar(
                    "SELECT COUNT(*) FROM torrent_stream ts \
                     WHERE NOT EXISTS (SELECT 1 FROM stream s WHERE s.id = ts.stream_id)",
                )
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);

                if dry_run {
                    would_delete.insert("torrent_stream".to_string(), count);
                } else {
                    match sqlx::query(
                        "DELETE FROM torrent_stream ts \
                         WHERE NOT EXISTS (SELECT 1 FROM stream s WHERE s.id = ts.stream_id)",
                    )
                    .execute(&state.pool)
                    .await
                    {
                        Ok(r) => {
                            deleted.insert("torrent_stream".to_string(), r.rows_affected() as i64);
                        }
                        Err(e) => {
                            tracing::error!("cleanup torrent_stream: {e}");
                        }
                    }
                }
            }
            "stream" => {
                // Deep cascade: first count orphan streams
                let count: i64 = sqlx::query_scalar(
                    "SELECT COUNT(*) FROM stream s \
                     WHERE NOT EXISTS (SELECT 1 FROM stream_media_link sml WHERE sml.stream_id = s.id)",
                )
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);

                if dry_run {
                    would_delete.insert("stream".to_string(), count);
                } else {
                    // Delete child records first, then the stream
                    let orphan_ids: Vec<Value> = sqlx::query_scalar::<_, Value>(
                        "SELECT row_to_json(t) FROM \
                         (SELECT s.id FROM stream s \
                          WHERE NOT EXISTS (SELECT 1 FROM stream_media_link sml WHERE sml.stream_id = s.id)) t",
                    )
                    .fetch_all(&state.pool_ro)
                    .await
                    .unwrap_or_default();

                    // We use a single batched delete via CTE for efficiency
                    let result = sqlx::query(
                        "WITH orphan_streams AS (
                             SELECT s.id FROM stream s
                             WHERE NOT EXISTS (SELECT 1 FROM stream_media_link sml WHERE sml.stream_id = s.id)
                         )
                         DELETE FROM stream WHERE id IN (SELECT id FROM orphan_streams)",
                    )
                    .execute(&state.pool)
                    .await;

                    let _ = orphan_ids; // used for logging if needed

                    match result {
                        Ok(r) => {
                            deleted.insert("stream".to_string(), r.rows_affected() as i64);
                        }
                        Err(e) => {
                            tracing::error!("cleanup stream: {e}");
                        }
                    }
                }
            }
            "stream_media_link" => {
                let count: i64 = sqlx::query_scalar(
                    "SELECT COUNT(*) FROM stream_media_link sml \
                     WHERE NOT EXISTS (SELECT 1 FROM media m WHERE m.id = sml.meta_id)",
                )
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);

                if dry_run {
                    would_delete.insert("stream_media_link".to_string(), count);
                } else {
                    match sqlx::query(
                        "DELETE FROM stream_media_link sml \
                         WHERE NOT EXISTS (SELECT 1 FROM media m WHERE m.id = sml.meta_id)",
                    )
                    .execute(&state.pool)
                    .await
                    {
                        Ok(r) => {
                            deleted
                                .insert("stream_media_link".to_string(), r.rows_affected() as i64);
                        }
                        Err(e) => {
                            tracing::error!("cleanup stream_media_link: {e}");
                        }
                    }
                }
            }
            _ => {
                tracing::warn!("cleanup_orphans: unknown table {table}");
            }
        }
    }

    Json(json!({
        "dry_run": dry_run,
        "deleted": deleted,
        "would_delete": would_delete,
    }))
    .into_response()
}

/// GET /api/v1/admin/db/orphans/streams (legacy)
pub async fn detect_orphan_streams(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let timeout = "SET LOCAL statement_timeout = '30s'";
    let count: Option<i64> = async {
        let mut tx = state.pool_ro.begin().await.ok()?;
        sqlx::query(timeout).execute(&mut *tx).await.ok()?;
        let n: i64 = sqlx::query_scalar(
            r#"SELECT COUNT(*) FROM stream s
               WHERE NOT EXISTS (SELECT 1 FROM stream_media_link sml WHERE sml.stream_id = s.id)
                 AND NOT EXISTS (SELECT 1 FROM stream_file sf JOIN file_media_link fml ON fml.file_id = sf.id WHERE sf.stream_id = s.id)"#,
        )
        .fetch_one(&mut *tx)
        .await
        .ok()?;
        Some(n)
    }
    .await;

    Json(json!({"orphan_streams_count": count.unwrap_or(-1)})).into_response()
}

/// GET /api/v1/admin/db/orphans/media (legacy)
pub async fn detect_orphan_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let timeout = "SET LOCAL statement_timeout = '30s'";
    let count: Option<i64> = async {
        let mut tx = state.pool_ro.begin().await.ok()?;
        sqlx::query(timeout).execute(&mut *tx).await.ok()?;
        let n: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM media WHERE total_streams = 0")
            .fetch_one(&mut *tx)
            .await
            .ok()?;
        Some(n)
    }
    .await;

    Json(json!({"orphan_media_count": count.unwrap_or(-1)})).into_response()
}
