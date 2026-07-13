//! Bulk import of IMDb non-commercial datasets into the existing Postgres schema.
//!
//! Pipeline per dataset: stream download → gzip stream → COPY staging → set-based merge.

mod copy;
mod download;
mod merge;
mod types;

use std::path::Path;

use async_trait::async_trait;
use chrono::Utc;
use fred::clients::Client as RedisClient;
use fred::prelude::{Expiration, KeysInterface};
use serde::Deserialize;
use tracing::{info, warn};

use self::types::{DatasetDef, ImportStatus, STATUS_REDIS_KEY};
use crate::jobs::{
    error::JobError,
    handler::{JobCtx, JobHandler},
};

// Status TTL: 24 h. Prevents stale "Running" display after a crash/restart.
const STATUS_TTL_SECS: i64 = 86_400;

pub struct ImdbDatasetImport;

#[derive(Debug, Default, Deserialize)]
pub struct ImdbImportArgs {
    /// Subset of datasets to process (default: cron payload, then env, then all).
    pub datasets: Option<Vec<String>>,
    /// Re-download even when the server returns 304 Not Modified.
    #[serde(default)]
    pub force: bool,
    /// Include adult titles in basics merge (overrides config when set).
    pub include_adult: Option<bool>,
    /// Skip download/COPY and merge from existing staging tables (dev/retry).
    #[serde(default)]
    pub merge_only: bool,
    /// Directory with pre-downloaded IMDb `.tsv.gz` files (skips HTTP download).
    pub local_dir: Option<String>,
}

/// Reclaim import jobs left `running` after a worker crash/kill.
pub async fn reclaim_stale_import_jobs(pool: &sqlx::PgPool) {
    let _ = sqlx::query(
        r#"UPDATE jobs
           SET status = 'dead',
               finished_at = COALESCE(started_at, now()),
               last_error = 'Worker exited — import interrupted'
           WHERE queue = 'imdb_dataset_import'
             AND status = 'running'
             AND started_at < now() - interval '15 minutes'
             AND NOT EXISTS (
                 SELECT 1 FROM pg_stat_activity
                 WHERE datname = current_database()
                   AND state = 'active'
                   AND pid <> pg_backend_pid()
                   AND query ILIKE '%imdb_stage_%'
             )"#,
    )
    .execute(pool)
    .await;
}

/// Returns a currently active import job id, if any.
pub async fn active_import_job_id(pool: &sqlx::PgPool) -> Result<Option<i64>, JobError> {
    reclaim_stale_import_jobs(pool).await;
    let id: Option<i64> = sqlx::query_scalar(
        r#"SELECT id FROM jobs
           WHERE queue = 'imdb_dataset_import'
             AND status = 'running'
             AND started_at > now() - interval '12 hours'
           ORDER BY started_at DESC
           LIMIT 1"#,
    )
    .fetch_optional(pool)
    .await?;
    Ok(id)
}

#[async_trait]
impl JobHandler for ImdbDatasetImport {
    const QUEUE: &'static str = "imdb_dataset_import";
    const CONCURRENCY: usize = 1;
    const MAX_ATTEMPTS: i32 = 1;
    type Args = ImdbImportArgs;

    async fn run(&self, args: ImdbImportArgs, ctx: JobCtx) -> Result<(), JobError> {
        let cfg = &ctx.state.config;
        let include_adult = args.include_adult.unwrap_or(cfg.imdb_import_include_adult);

        let datasets = types::resolve_datasets(args.datasets.as_deref(), &cfg.imdb_import_datasets);

        if datasets.is_empty() {
            warn!("imdb_dataset_import: no datasets selected, nothing to do");
            return Ok(());
        }

        let providers = merge::load_provider_ids(&ctx.state.pool).await?;
        let started_at = Utc::now().to_rfc3339();

        let result = run_inner(
            &ctx,
            args,
            include_adult,
            datasets,
            providers,
            started_at.clone(),
        )
        .await;

        if let Err(ref e) = result {
            let phase = if matches!(e, JobError::Cancelled) {
                "cancelled"
            } else {
                "error"
            };
            tracing::error!(error = %e, phase, "imdb_dataset_import failed");
            write_status(
                &ctx.state.redis,
                ImportStatus {
                    message: Some(e.to_string()),
                    ..base_status(phase, &started_at)
                },
            )
            .await;
        }

        result
    }
}

async fn run_inner(
    ctx: &JobCtx,
    args: ImdbImportArgs,
    include_adult: bool,
    datasets: Vec<&'static types::DatasetDef>,
    providers: merge::ProviderIds,
    started_at: String,
) -> Result<(), JobError> {
    let cfg = &ctx.state.config;

    write_status(
        &ctx.state.redis,
        ImportStatus {
            message: Some(format!("datasets: {}", datasets.len())),
            ..base_status("starting", &started_at)
        },
    )
    .await;

    info!(
        datasets = ?datasets.iter().map(|d| d.key).collect::<Vec<_>>(),
        include_adult,
        force = args.force,
        merge_only = args.merge_only,
        local_dir = args.local_dir.as_deref().unwrap_or(""),
        "imdb_dataset_import: starting"
    );

    for dataset in datasets {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        let rows_loaded = match prepare_dataset_rows(ctx, &args, dataset, cfg, &started_at).await? {
            Some(rows) => rows,
            None => continue,
        };

        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        write_status(
            &ctx.state.redis,
            ImportStatus {
                dataset: Some(dataset.key.to_string()),
                rows_loaded: Some(rows_loaded),
                ..base_status("merge", &started_at)
            },
        )
        .await;

        let rows_merged = merge::merge_dataset(
            &ctx.state.pool,
            &ctx.state.redis,
            dataset.key,
            rows_loaded,
            &started_at,
            &providers,
            include_adult,
            &ctx.cancel,
        )
        .await?;

        record_merge_result(&ctx.state.pool, dataset.key, rows_merged as i64).await?;

        info!(
            dataset = dataset.key,
            rows_loaded, rows_merged, "imdb_dataset_import: dataset complete"
        );

        write_status(
            &ctx.state.redis,
            ImportStatus {
                phase: "dataset_done".into(),
                dataset: Some(dataset.key.to_string()),
                rows_loaded: Some(rows_loaded),
                rows_merged: Some(rows_merged as i64),
                started_at: started_at.clone(),
                ..base_status("dataset_done", &started_at)
            },
        )
        .await;
    }

    write_status(&ctx.state.redis, base_status("complete", &started_at)).await;

    info!("imdb_dataset_import: all datasets complete");
    Ok(())
}

/// Download/COPY (or reuse staging) for one dataset. Returns `None` when the dataset can be skipped.
async fn prepare_dataset_rows(
    ctx: &JobCtx,
    args: &ImdbImportArgs,
    dataset: &DatasetDef,
    cfg: &crate::config::AppConfig,
    started_at: &str,
) -> Result<Option<i64>, JobError> {
    if let Some(ref dir) = args.local_dir {
        let path = Path::new(dir).join(dataset.file_name);
        if !path.is_file() {
            return Err(JobError::other(format!(
                "local IMDb file not found: {} (expected {})",
                path.display(),
                dataset.file_name
            )));
        }

        info!(
            dataset = dataset.key,
            path = %path.display(),
            "copying local IMDb dataset into staging"
        );

        write_status(
            &ctx.state.redis,
            ImportStatus {
                dataset: Some(dataset.key.to_string()),
                message: Some(path.display().to_string()),
                ..base_status("copy", started_at)
            },
        )
        .await;

        let loaded = copy::copy_into_staging(&ctx.state.pool, dataset, &path).await?;
        return Ok(Some(loaded));
    }

    if args.merge_only {
        let staged = copy::staging_row_count(&ctx.state.pool, dataset).await?;
        if staged == 0 {
            return Err(JobError::other(format!(
                "staging table {} is empty — re-download required (UNLOGGED staging is cleared on Postgres restart)",
                dataset.staging_table
            )));
        }
        return Ok(Some(staged));
    }

    let mut force = args.force;

    loop {
        write_status(
            &ctx.state.redis,
            ImportStatus {
                dataset: Some(dataset.key.to_string()),
                message: if force && !args.force {
                    Some("Re-downloading — staging data was lost".into())
                } else {
                    None
                },
                ..base_status("download", started_at)
            },
        )
        .await;

        let dl = download::download_dataset(
            &ctx.state.http,
            &ctx.state.pool,
            &cfg.imdb_datasets_base_url,
            dataset,
            force,
        )
        .await?;

        if !dl.skipped {
            write_status(
                &ctx.state.redis,
                ImportStatus {
                    dataset: Some(dataset.key.to_string()),
                    ..base_status("copy", started_at)
                },
            )
            .await;

            let loaded = copy::copy_into_staging(&ctx.state.pool, dataset, &dl.path).await?;
            download::cleanup_temp(&dl.path).await;
            return Ok(Some(loaded));
        }

        let staged = copy::staging_row_count(&ctx.state.pool, dataset).await?;
        let recorded_rows = sqlx::query_scalar::<_, i64>(
            "SELECT COALESCE(rows_loaded, 0) FROM imdb_import_state WHERE dataset = $1",
        )
        .bind(dataset.key)
        .fetch_optional(&ctx.state.pool)
        .await?
        .unwrap_or(0);
        let rows_merged: Option<i64> =
            sqlx::query_scalar("SELECT rows_merged FROM imdb_import_state WHERE dataset = $1")
                .bind(dataset.key)
                .fetch_optional(&ctx.state.pool)
                .await?;

        if staged == 0 && recorded_rows > 0 && !force {
            warn!(
                dataset = dataset.key,
                recorded_rows,
                "IMDb staging empty but import state shows prior COPY — forcing re-download"
            );
            force = true;
            continue;
        }

        if staged == 0 {
            info!(
                dataset = dataset.key,
                "dataset unchanged and staging empty — skipping"
            );
            write_status(
                &ctx.state.redis,
                ImportStatus {
                    dataset: Some(dataset.key.to_string()),
                    message: Some("304 Not Modified (no staging data)".into()),
                    ..base_status("skipped", started_at)
                },
            )
            .await;
            return Ok(None);
        }

        if rows_merged.is_some() && !args.force {
            info!(
                dataset = dataset.key,
                staged, "dataset unchanged — staging present and merge already recorded"
            );
            write_status(
                &ctx.state.redis,
                ImportStatus {
                    dataset: Some(dataset.key.to_string()),
                    rows_loaded: Some(staged),
                    rows_merged,
                    message: Some("304 Not Modified".into()),
                    ..base_status("skipped", started_at)
                },
            )
            .await;
            return Ok(None);
        }

        info!(
            dataset = dataset.key,
            staged, "dataset unchanged on server — merging from existing staging"
        );
        return Ok(Some(staged));
    }
}

async fn record_merge_result(
    pool: &sqlx::PgPool,
    dataset: &str,
    rows_merged: i64,
) -> Result<(), JobError> {
    sqlx::query(
        r#"INSERT INTO imdb_import_state (dataset, last_run_at, rows_merged)
           VALUES ($1, now(), $2)
           ON CONFLICT (dataset) DO UPDATE SET
             rows_merged = EXCLUDED.rows_merged,
             last_run_at = now()"#,
    )
    .bind(dataset)
    .bind(rows_merged)
    .execute(pool)
    .await?;
    Ok(())
}

fn base_status(phase: &str, started_at: &str) -> ImportStatus {
    ImportStatus {
        phase: phase.into(),
        dataset: None,
        merge_step: None,
        rows_loaded: None,
        rows_merged: None,
        rows_processed: None,
        rows_total: None,
        started_at: started_at.to_string(),
        message: None,
    }
}

async fn write_status(redis: &RedisClient, status: ImportStatus) {
    if let Ok(json) = serde_json::to_string(&status) {
        let _: Result<(), _> = redis
            .set::<(), _, _>(
                STATUS_REDIS_KEY,
                json,
                Some(Expiration::EX(STATUS_TTL_SECS)),
                None,
                false,
            )
            .await;
    }
}
