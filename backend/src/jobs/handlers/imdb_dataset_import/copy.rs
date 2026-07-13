use std::io::{BufRead, BufReader};
use std::path::Path;

use flate2::read::GzDecoder;
use sqlx::pool::PoolConnection;
use sqlx::{PgPool, Postgres};
use tracing::info;

use super::types::DatasetDef;
use crate::jobs::error::JobError;

const COPY_BUF_LINES: usize = 8_192;
const COPY_FLUSH_BYTES: usize = 4 * 1024 * 1024;

async fn begin_long_running_conn(conn: &mut PoolConnection<Postgres>) -> Result<(), JobError> {
    sqlx::query("BEGIN").execute(&mut **conn).await?;
    sqlx::query("SET LOCAL statement_timeout = '0'")
        .execute(&mut **conn)
        .await?;
    sqlx::query("SET LOCAL idle_in_transaction_session_timeout = '0'")
        .execute(&mut **conn)
        .await?;
    Ok(())
}

async fn rollback_conn(conn: &mut PoolConnection<Postgres>) {
    let _ = sqlx::query("ROLLBACK").execute(&mut **conn).await;
}

pub async fn staging_row_count(pool: &PgPool, dataset: &DatasetDef) -> Result<i64, JobError> {
    let sql = format!("SELECT COUNT(*)::bigint FROM {}", dataset.staging_table);
    let mut conn = pool.acquire().await?;
    begin_long_running_conn(&mut conn).await?;
    let count = sqlx::query_scalar::<_, i64>(sqlx::AssertSqlSafe(sql.as_str()))
        .fetch_one(&mut *conn)
        .await?;
    sqlx::query("COMMIT").execute(&mut *conn).await?;
    Ok(count)
}

/// TRUNCATE staging, stream-decompress the gz TSV, COPY into Postgres.
pub async fn copy_into_staging(
    pool: &PgPool,
    dataset: &DatasetDef,
    gz_path: &Path,
) -> Result<i64, JobError> {
    let table = dataset.staging_table.to_string();
    let columns = dataset.copy_columns.to_string();
    let path = gz_path.to_path_buf();

    let (tx, mut rx) = tokio::sync::mpsc::channel::<String>(COPY_BUF_LINES);

    let reader_handle = tokio::task::spawn_blocking(move || -> Result<(), JobError> {
        let file =
            std::fs::File::open(&path).map_err(|e| JobError::other(format!("open gz: {e}")))?;
        let decoder = GzDecoder::new(file);
        let mut reader = BufReader::new(decoder);
        let mut line = String::new();
        reader
            .read_line(&mut line)
            .map_err(|e| JobError::other(format!("read header: {e}")))?;
        line.clear();
        loop {
            line.clear();
            let n = reader
                .read_line(&mut line)
                .map_err(|e| JobError::other(format!("read line: {e}")))?;
            if n == 0 {
                break;
            }
            tx.blocking_send(std::mem::take(&mut line))
                .map_err(|_| JobError::other("copy channel closed"))?;
        }
        Ok(())
    });

    let copy_sql = format!("COPY {table} ({columns}) FROM STDIN");
    let mut conn = pool.acquire().await?;
    begin_long_running_conn(&mut conn).await?;

    let copy_result: Result<i64, JobError> = async {
        sqlx::query(sqlx::AssertSqlSafe(format!("TRUNCATE {table}")))
            .execute(&mut *conn)
            .await?;

        let mut copy_in = conn.copy_in_raw(&copy_sql).await?;

        let mut row_count: i64 = 0;
        let mut batch = String::with_capacity(COPY_FLUSH_BYTES.min(256 * 1024));

        while let Some(line) = rx.recv().await {
            batch.push_str(&line);
            row_count += 1;
            if batch.len() >= COPY_FLUSH_BYTES {
                copy_in.send(batch.as_bytes()).await?;
                batch.clear();
            }
        }

        if !batch.is_empty() {
            copy_in.send(batch.as_bytes()).await?;
        }

        reader_handle
            .await
            .map_err(|e| JobError::other(format!("reader task: {e}")))??;

        copy_in.finish().await?;

        sqlx::query(sqlx::AssertSqlSafe(format!("ANALYZE {table}")))
            .execute(&mut *conn)
            .await?;

        sqlx::query(
            "UPDATE imdb_import_state SET rows_loaded = $2, rows_merged = NULL WHERE dataset = $1",
        )
        .bind(dataset.key)
        .bind(row_count)
        .execute(&mut *conn)
        .await?;

        Ok(row_count)
    }
    .await;

    if copy_result.is_err() {
        rollback_conn(&mut conn).await;
        return copy_result;
    }

    sqlx::query("COMMIT").execute(&mut *conn).await?;
    let row_count = copy_result?;

    info!(
        dataset = dataset.key,
        rows = row_count,
        "COPY into staging complete"
    );

    Ok(row_count)
}
