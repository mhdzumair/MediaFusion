use std::io::{BufRead, BufReader};
use std::path::Path;

use flate2::read::GzDecoder;
use sqlx::PgPool;
use tracing::info;

use super::types::DatasetDef;
use crate::jobs::error::JobError;

const COPY_BUF_LINES: usize = 8_192;
const COPY_FLUSH_BYTES: usize = 4 * 1024 * 1024;

/// TRUNCATE staging, stream-decompress the gz TSV, COPY into Postgres.
pub async fn copy_into_staging(
    pool: &PgPool,
    dataset: &DatasetDef,
    gz_path: &Path,
) -> Result<i64, JobError> {
    sqlx::query(&format!("TRUNCATE {}", dataset.staging_table))
        .execute(pool)
        .await?;

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

    sqlx::query(&format!("ANALYZE {}", dataset.staging_table))
        .execute(pool)
        .await?;

    info!(
        dataset = dataset.key,
        rows = row_count,
        "COPY into staging complete"
    );

    sqlx::query("UPDATE imdb_import_state SET rows_loaded = $2 WHERE dataset = $1")
        .bind(dataset.key)
        .bind(row_count)
        .execute(pool)
        .await?;

    Ok(row_count)
}
