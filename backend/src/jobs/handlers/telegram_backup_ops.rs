//! Admin jobs: copy Telegram streams to the backup channel or restore DB rows from it.

use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Arc;
use tracing::info;

use grammers_client::Client;
use grammers_session::types::PeerRef;

use crate::{
    db::{
        telegram::{TelegramStreamBackupRow, list_streams_for_backup_store},
        types::UserId,
    },
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    scrapers::telegram_clients::is_auth_key_duplicated,
    services::telegram_backup::{
        BackupBatchMetrics, resolve_bot_mtproto_client, resolve_mtproto_client,
        restore_stream_from_backup_message, store_stream_to_backup,
    },
    services::telegram_peer,
};

pub struct TelegramBackupStore;

pub struct TelegramBackupRestore;

#[derive(Debug, serde::Deserialize, serde::Serialize)]
pub struct TelegramBackupStoreArgs {
    #[serde(default)]
    pub mediafusion_user_id: Option<i32>,
    #[serde(default = "default_only_missing")]
    pub only_missing: bool,
    #[serde(default = "default_batch_size")]
    pub batch_size: i64,
    #[serde(default)]
    pub after_id: i32,
    #[serde(default = "default_continuous")]
    pub continuous: bool,
    #[serde(default)]
    pub capture_file_id: bool,
    #[serde(default)]
    pub max_batches: Option<i64>,
}

#[derive(Debug, serde::Deserialize, serde::Serialize)]
pub struct TelegramBackupRestoreArgs {
    #[serde(default)]
    pub mediafusion_user_id: Option<i32>,
    #[serde(default = "default_message_limit")]
    pub message_limit: i32,
    #[serde(default = "default_capture_file_id")]
    pub capture_file_id: bool,
}

fn default_only_missing() -> bool {
    true
}

fn default_batch_size() -> i64 {
    25
}

fn default_continuous() -> bool {
    true
}

fn default_message_limit() -> i32 {
    500
}

fn default_capture_file_id() -> bool {
    true
}

async fn load_user_session(
    ctx: &JobCtx,
    preferred: Option<UserId>,
) -> (Option<Arc<Client>>, Option<HashMap<i64, PeerRef>>) {
    for attempt in 0..2 {
        let session = resolve_mtproto_client(&ctx.state, preferred).await.ok();
        let Some((user_id, client)) = session else {
            return (None, None);
        };

        let (dialog_peers, dialog_error) =
            telegram_peer::load_dialog_peer_map(client.as_ref()).await;
        if let Some(err) = dialog_error.as_deref()
            && attempt == 0
            && is_auth_key_duplicated(err)
        {
            tracing::warn!(
                "telegram_backup_store: AUTH_KEY_DUPLICATED for user {} — recycling client",
                user_id.0
            );
            ctx.state.telegram_clients.invalidate(user_id).await;
            telegram_peer::invalidate_dialog_peer_cache(user_id).await;
            continue;
        }

        return (Some(client), Some(dialog_peers));
    }

    (None, None)
}

async fn run_backup_store_batch(
    ctx: &JobCtx,
    args: &TelegramBackupStoreArgs,
    user_client: Option<&Client>,
    user_dialog_peers: Option<&HashMap<i64, PeerRef>>,
    after_id: i32,
) -> Result<(BackupBatchMetrics, i32), JobError> {
    let batch_size = args.batch_size.clamp(1, 200);
    let rows =
        list_streams_for_backup_store(&ctx.state.pool, after_id, batch_size, args.only_missing)
            .await;

    if rows.is_empty() {
        return Ok((BackupBatchMetrics::default(), after_id));
    }

    let mut metrics = BackupBatchMetrics::default();
    let mut last_id = after_id;

    for row in rows {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }
        last_id = row.id;
        metrics.processed += 1;
        match store_one(
            ctx,
            user_client,
            user_dialog_peers,
            &row,
            args.capture_file_id,
        )
        .await
        {
            Ok(true) => metrics.stored += 1,
            Ok(false) => metrics.skipped += 1,
            Err(e) => {
                tracing::warn!("telegram_backup_store: stream {} — {e}", row.id);
                metrics.errors += 1;
            }
        }
    }

    Ok((metrics, last_id))
}

async fn store_one(
    ctx: &JobCtx,
    user_client: Option<&Client>,
    user_dialog_peers: Option<&HashMap<i64, PeerRef>>,
    row: &TelegramStreamBackupRow,
    capture_file_id: bool,
) -> Result<bool, String> {
    store_stream_to_backup(
        &ctx.state,
        user_client,
        user_dialog_peers,
        row,
        capture_file_id,
    )
    .await?;
    Ok(true)
}

#[async_trait]
impl JobHandler for TelegramBackupStore {
    const QUEUE: &'static str = "telegram_backup_store";
    const CONCURRENCY: usize = 1;
    type Args = TelegramBackupStoreArgs;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        if ctx.state.config.telegram_bot_token.is_none()
            && !ctx.state.telegram_clients.api_configured()
        {
            return Err(JobError::other(
                "Configure TELEGRAM_BOT_TOKEN or Telegram API credentials with a scraping session",
            ));
        }
        if ctx
            .state
            .config
            .telegram_backup_channel_id
            .as_deref()
            .filter(|s| !s.is_empty())
            .is_none()
        {
            return Err(JobError::other(
                "TELEGRAM_BACKUP_CHANNEL_ID is not configured",
            ));
        }

        let preferred = args.mediafusion_user_id.map(UserId);
        let (user_client, user_dialog_peers) = load_user_session(&ctx, preferred).await;

        let mut after_id = args.after_id;
        let mut totals = BackupBatchMetrics::default();
        let mut batches_done: i64 = 0;

        loop {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let (batch, last_id) = run_backup_store_batch(
                &ctx,
                &args,
                user_client.as_deref(),
                user_dialog_peers.as_ref(),
                after_id,
            )
            .await?;
            if batch.processed == 0 {
                break;
            }

            totals.processed += batch.processed;
            totals.stored += batch.stored;
            totals.skipped += batch.skipped;
            totals.errors += batch.errors;
            after_id = last_id;
            batches_done += 1;

            if !args.continuous {
                break;
            }
            if let Some(max) = args.max_batches
                && batches_done >= max
            {
                break;
            }
        }

        info!(
            "telegram_backup_store: processed {} stored {} skipped {} errors {} (last_id={})",
            totals.processed, totals.stored, totals.skipped, totals.errors, after_id
        );
        Ok(())
    }
}

#[async_trait]
impl JobHandler for TelegramBackupRestore {
    const QUEUE: &'static str = "telegram_backup_restore";
    const CONCURRENCY: usize = 1;
    type Args = TelegramBackupRestoreArgs;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        if ctx.state.config.telegram_bot_token.is_none() {
            return Err(JobError::other("TELEGRAM_BOT_TOKEN is not configured"));
        }
        if !ctx.state.telegram_clients.api_configured() {
            return Err(JobError::other(
                "Telegram API credentials are not configured",
            ));
        }

        let backup_channel = ctx
            .state
            .config
            .telegram_backup_channel_id
            .as_deref()
            .filter(|s| !s.is_empty())
            .ok_or_else(|| JobError::other("TELEGRAM_BACKUP_CHANNEL_ID is not configured"))?;

        let _ = args.mediafusion_user_id;
        let client = resolve_bot_mtproto_client(&ctx.state)
            .await
            .map_err(JobError::other)?;

        let (dialog_peers, _) = telegram_peer::load_dialog_peer_map(client.as_ref()).await;
        let (_, backup_peer_ref) = telegram_peer::resolve_channel_peer(
            client.as_ref(),
            backup_channel,
            &dialog_peers,
        )
        .await
        .ok_or_else(|| {
            JobError::other(format!(
                "backup channel {backup_channel} is not accessible to the bot — add the bot as admin with read history"
            ))
        })?;

        let mut iter = client.iter_messages(backup_peer_ref);
        if args.message_limit > 0 {
            iter = iter.limit(args.message_limit as usize);
        }

        let mut metrics = BackupBatchMetrics::default();

        loop {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }
            match iter.next().await {
                Ok(Some(message)) => {
                    metrics.processed += 1;
                    match restore_stream_from_backup_message(
                        &ctx.state,
                        backup_channel,
                        &message,
                        args.capture_file_id,
                    )
                    .await
                    {
                        Ok(Some(_)) => metrics.restored += 1,
                        Ok(None) => metrics.skipped += 1,
                        Err(e) => {
                            tracing::debug!("telegram_backup_restore: msg {} — {e}", message.id());
                            metrics.skipped += 1;
                        }
                    }
                }
                Ok(None) => break,
                Err(e) => {
                    tracing::warn!("telegram_backup_restore: iter_messages — {e}");
                    metrics.errors += 1;
                    break;
                }
            }
        }

        info!(
            "telegram_backup_restore: scanned {} restored {} skipped {} errors {}",
            metrics.processed, metrics.restored, metrics.skipped, metrics.errors
        );
        Ok(())
    }
}
