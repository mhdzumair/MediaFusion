use std::path::{Path, PathBuf};
use std::time::Duration;

use once_cell::sync::Lazy;
use reqwest::Client;
use reqwest::header::{IF_MODIFIED_SINCE, IF_NONE_MATCH};
use sqlx::PgPool;
use tokio::io::AsyncWriteExt;
use tracing::info;

use super::types::DatasetDef;
use crate::jobs::error::JobError;

/// IMDb datasets are multi-GB; the shared app HTTP client uses a 30s timeout.
static IMDB_HTTP: Lazy<Client> = Lazy::new(|| {
    Client::builder()
        .user_agent("mediafusion-imdb-import/1.0")
        .connect_timeout(Duration::from_secs(30))
        .timeout(Duration::from_secs(7200))
        .tcp_keepalive(Duration::from_secs(60))
        .build()
        .expect("imdb import HTTP client")
});

pub struct DownloadResult {
    pub path: PathBuf,
    pub skipped: bool,
}

/// Stream-download a dataset `.tsv.gz` to a temp file.
/// Returns `skipped = true` when the server responds 304 Not Modified.
pub async fn download_dataset(
    _http: &reqwest::Client,
    pool: &PgPool,
    base_url: &str,
    dataset: &DatasetDef,
    force: bool,
) -> Result<DownloadResult, JobError> {
    let http = &*IMDB_HTTP;
    let url = format!("{}/{}", base_url.trim_end_matches('/'), dataset.file_name);
    let mut req = http.get(&url);

    if !force {
        let state = sqlx::query_as::<_, (Option<String>, Option<String>)>(
            "SELECT etag, last_modified FROM imdb_import_state WHERE dataset = $1",
        )
        .bind(dataset.key)
        .fetch_optional(pool)
        .await?;

        if let Some((etag, last_modified)) = state {
            if let Some(e) = etag.filter(|s| !s.is_empty()) {
                req = req.header(IF_NONE_MATCH, e);
            }
            if let Some(lm) = last_modified.filter(|s| !s.is_empty()) {
                req = req.header(IF_MODIFIED_SINCE, lm);
            }
        }
    }

    let mut resp = req.send().await?;

    if resp.status() == reqwest::StatusCode::NOT_MODIFIED {
        info!(dataset = dataset.key, "dataset unchanged (304), skipping");
        return Ok(DownloadResult {
            path: PathBuf::new(),
            skipped: true,
        });
    }

    if !resp.status().is_success() {
        return Err(JobError::other(format!(
            "IMDb download {} returned HTTP {}",
            url,
            resp.status()
        )));
    }

    let etag = resp
        .headers()
        .get("etag")
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    let last_modified = resp
        .headers()
        .get("last-modified")
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);

    let suffix = dataset.file_name.replace('.', "_");
    let tmp = tempfile::Builder::new()
        .prefix("imdb_")
        .suffix(&suffix)
        .tempfile()
        .map_err(|e| JobError::other(format!("tempfile: {e}")))?;
    let path = tmp.path().to_path_buf();

    let mut file = tokio::fs::File::create(&path)
        .await
        .map_err(|e| JobError::other(format!("create temp file: {e}")))?;

    while let Some(chunk) = resp
        .chunk()
        .await
        .map_err(|e| JobError::other(format!("download chunk: {e}")))?
    {
        file.write_all(&chunk)
            .await
            .map_err(|e| JobError::other(format!("write temp file: {e}")))?;
    }
    file.flush()
        .await
        .map_err(|e| JobError::other(format!("flush temp file: {e}")))?;
    drop(file);

    let _ = tmp.keep();

    sqlx::query(
        r#"INSERT INTO imdb_import_state (dataset, etag, last_modified, last_run_at)
           VALUES ($1, $2, $3, now())
           ON CONFLICT (dataset) DO UPDATE SET
             etag = COALESCE(EXCLUDED.etag, imdb_import_state.etag),
             last_modified = COALESCE(EXCLUDED.last_modified, imdb_import_state.last_modified),
             last_run_at = now()"#,
    )
    .bind(dataset.key)
    .bind(etag)
    .bind(last_modified)
    .execute(pool)
    .await?;

    Ok(DownloadResult {
        path,
        skipped: false,
    })
}

pub async fn cleanup_temp(path: &Path) {
    if path.as_os_str().is_empty() {
        return;
    }
    let _ = tokio::fs::remove_file(path).await;
}
