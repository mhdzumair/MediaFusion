//! Bulk import of IMDb non-commercial datasets into the existing Postgres schema.
//!
//! Pipeline per dataset: stream download → gzip stream → COPY staging → set-based merge.

mod copy;
mod download;
mod merge;
mod types;

use async_trait::async_trait;
use chrono::Utc;
use fred::clients::Client as RedisClient;
use fred::prelude::{Expiration, KeysInterface};
use serde::Deserialize;
use tracing::{info, warn};

use self::types::{ImportStatus, STATUS_REDIS_KEY};
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
            write_status(
                &ctx.state.redis,
                ImportStatus {
                    phase: phase.into(),
                    dataset: None,
                    rows_loaded: None,
                    rows_merged: None,
                    started_at,
                    message: Some(e.to_string()),
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
            phase: "starting".into(),
            dataset: None,
            rows_loaded: None,
            rows_merged: None,
            started_at: started_at.clone(),
            message: Some(format!("datasets: {}", datasets.len())),
        },
    )
    .await;

    info!(
        datasets = ?datasets.iter().map(|d| d.key).collect::<Vec<_>>(),
        include_adult,
        force = args.force,
        merge_only = args.merge_only,
        "imdb_dataset_import: starting"
    );

    for dataset in datasets {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        let rows_loaded = if args.merge_only {
            sqlx::query_scalar::<_, i64>(
                "SELECT COALESCE(rows_loaded, 0) FROM imdb_import_state WHERE dataset = $1",
            )
            .bind(dataset.key)
            .fetch_optional(&ctx.state.pool)
            .await?
            .unwrap_or(0)
        } else {
            write_status(
                &ctx.state.redis,
                ImportStatus {
                    phase: "download".into(),
                    dataset: Some(dataset.key.to_string()),
                    rows_loaded: None,
                    rows_merged: None,
                    started_at: started_at.clone(),
                    message: None,
                },
            )
            .await;

            let dl = download::download_dataset(
                &ctx.state.http,
                &ctx.state.pool,
                &cfg.imdb_datasets_base_url,
                dataset,
                args.force,
            )
            .await?;

            if dl.skipped {
                info!(
                    dataset = dataset.key,
                    "dataset unchanged — skipping copy and merge"
                );
                write_status(
                    &ctx.state.redis,
                    ImportStatus {
                        phase: "skipped".into(),
                        dataset: Some(dataset.key.to_string()),
                        rows_loaded: None,
                        rows_merged: None,
                        started_at: started_at.clone(),
                        message: Some("304 Not Modified".into()),
                    },
                )
                .await;
                continue;
            }

            write_status(
                &ctx.state.redis,
                ImportStatus {
                    phase: "copy".into(),
                    dataset: Some(dataset.key.to_string()),
                    rows_loaded: None,
                    rows_merged: None,
                    started_at: started_at.clone(),
                    message: None,
                },
            )
            .await;

            let loaded = copy::copy_into_staging(&ctx.state.pool, dataset, &dl.path).await?;
            download::cleanup_temp(&dl.path).await;
            loaded
        };

        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        write_status(
            &ctx.state.redis,
            ImportStatus {
                phase: "merge".into(),
                dataset: Some(dataset.key.to_string()),
                rows_loaded: Some(rows_loaded),
                rows_merged: None,
                started_at: started_at.clone(),
                message: None,
            },
        )
        .await;

        let rows_merged =
            merge::merge_dataset(&ctx.state.pool, dataset.key, &providers, include_adult).await?;

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
                message: None,
            },
        )
        .await;
    }

    write_status(
        &ctx.state.redis,
        ImportStatus {
            phase: "complete".into(),
            dataset: None,
            rows_loaded: None,
            rows_merged: None,
            started_at,
            message: None,
        },
    )
    .await;

    info!("imdb_dataset_import: all datasets complete");
    Ok(())
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
