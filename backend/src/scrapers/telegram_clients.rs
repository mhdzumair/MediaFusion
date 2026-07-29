//! Per-user Telegram MTProto clients and shared bot MTProto client.
//!
//! User sessions use a Redis lock plus connect-use-disconnect per operation so multiple API
//! pods never hold the same auth key open simultaneously (Telegram returns AUTH_KEY_DUPLICATED).

use std::future::Future;
use std::sync::Arc;
use std::time::Duration;

use fred::prelude::{Expiration, KeysInterface, SetOptions};
use grammers_client::Client;
use grammers_client::sender::SenderPool;
use grammers_session::storages::MemorySession;
use std::sync::OnceLock;
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

use crate::{
    config::AppConfig,
    crypto::telegram_session as session_crypto,
    db::{self, types::UserId},
    util::telegram_session,
};

struct CachedClient {
    client: Arc<Client>,
    runner: JoinHandle<()>,
}

fn shutdown_client(client: &Client, runner: &JoinHandle<()>) {
    client.disconnect();
    runner.abort();
}

pub fn is_auth_key_duplicated(err: &str) -> bool {
    err.contains("AUTH_KEY_DUPLICATED")
}

const MTPROTO_LOCK_TTL_SECS: i64 = 900;
const MTPROTO_LOCK_WAIT: Duration = Duration::from_millis(200);
const MTPROTO_LOCK_ATTEMPTS: usize = 25;

fn mtproto_lock_key(user_id: UserId) -> String {
    format!("telegram:mtproto_lock:{}", user_id.0)
}

static BOT_CLIENT: OnceLock<Mutex<Option<Arc<CachedClient>>>> = OnceLock::new();

pub struct TelegramClientPool {
    config: AppConfig,
    redis: fred::clients::Client,
}

impl TelegramClientPool {
    pub fn new(config: AppConfig, redis: fred::clients::Client) -> Self {
        Self { config, redis }
    }

    pub fn api_configured(&self) -> bool {
        self.config.telegram_api_id.is_some() && self.config.telegram_api_hash.is_some()
    }

    /// Run `f` with a leased user MTProto client (Redis lock + disconnect on completion).
    pub async fn with_user_client<T, F, Fut>(
        &self,
        pool: &sqlx::PgPool,
        user_id: UserId,
        f: F,
    ) -> Result<T, String>
    where
        F: FnOnce(Arc<Client>) -> Fut,
        Fut: Future<Output = T>,
    {
        if !self.acquire_mtproto_lock(user_id).await {
            return Err(
                "Telegram session is busy on another request or server node — try again shortly"
                    .into(),
            );
        }

        let outcome = async {
            let cached = self.build_user_client(pool, user_id).await?;
            let output = f(Arc::clone(&cached.client)).await;
            shutdown_client(&cached.client, &cached.runner);
            Ok(output)
        }
        .await;

        self.release_mtproto_lock(user_id).await;

        outcome
    }

    /// Best-effort disconnect for login retries; also releases the Redis lease.
    pub async fn invalidate(&self, user_id: UserId) {
        self.release_mtproto_lock(user_id).await;
    }

    async fn acquire_mtproto_lock(&self, user_id: UserId) -> bool {
        for _ in 0..MTPROTO_LOCK_ATTEMPTS {
            let acquired: Option<String> = self
                .redis
                .set(
                    mtproto_lock_key(user_id),
                    "1",
                    Some(Expiration::EX(MTPROTO_LOCK_TTL_SECS)),
                    Some(SetOptions::NX),
                    false,
                )
                .await
                .ok()
                .flatten();
            if acquired.is_some() {
                return true;
            }
            tokio::time::sleep(MTPROTO_LOCK_WAIT).await;
        }
        false
    }

    async fn release_mtproto_lock(&self, user_id: UserId) {
        let _ = self
            .redis
            .del::<(), _>(mtproto_lock_key(user_id))
            .await;
    }

    async fn build_user_client(
        &self,
        pool: &sqlx::PgPool,
        user_id: UserId,
    ) -> Result<Arc<CachedClient>, String> {
        let api_id = self
            .config
            .telegram_api_id
            .ok_or_else(|| "Telegram API credentials are not configured".to_string())?;
        let api_hash = self
            .config
            .telegram_api_hash
            .as_deref()
            .ok_or_else(|| "Telegram API credentials are not configured".to_string())?;

        let row = db::user_telegram_session::get_session(pool, user_id)
            .await
            .ok_or_else(|| "Telegram scraping session is not connected".to_string())?;
        let session_plain = session_crypto::decrypt_session(&row.encrypted_session, &self.config.secret_key)
            .ok_or_else(|| "failed to decrypt Telegram session".to_string())?;
        let session_data = telegram_session::parse_session_data(&session_plain)
            .map_err(|_| "invalid Telegram session data".to_string())?;
        if !telegram_session::session_is_authenticated(&session_data) {
            return Err("Telegram session is not authenticated".into());
        }

        let entry = build_client(api_id, api_hash, session_data)
            .await
            .map_err(|e| format!("connect Telegram client: {e}"))?;
        db::user_telegram_session::touch_last_used(pool, user_id).await;
        Ok(entry)
    }

    /// MTProto client signed in with `TELEGRAM_BOT_TOKEN` for backup-channel operations.
    pub async fn get_bot_client(&self) -> Option<Arc<Client>> {
        if !self.api_configured() {
            return None;
        }
        let bot_token = self.config.telegram_bot_token.as_deref()?;
        let api_id = self.config.telegram_api_id?;
        let api_hash = self.config.telegram_api_hash.as_deref()?;

        let slot = BOT_CLIENT.get_or_init(|| Mutex::new(None));
        let mut guard = slot.lock().await;
        if let Some(cached) = guard.as_ref() {
            return Some(Arc::clone(&cached.client));
        }

        match build_bot_client(api_id, api_hash, bot_token).await {
            Ok(cached) => {
                let client = Arc::clone(&cached.client);
                *guard = Some(cached);
                Some(client)
            }
            Err(e) => {
                tracing::warn!("telegram: bot MTProto client init failed: {e}");
                None
            }
        }
    }
}

async fn build_bot_client(
    api_id: i32,
    api_hash: &str,
    bot_token: &str,
) -> Result<Arc<CachedClient>, Box<dyn std::error::Error + Send + Sync>> {
    let session = Arc::new(MemorySession::default());
    let pool = SenderPool::new(Arc::clone(&session) as Arc<_>, api_id);
    let runner = pool.runner;
    let handle = pool.handle;
    let runner_task = tokio::spawn(async move {
        runner.run().await;
    });
    let client = Client::new(handle);
    client.bot_sign_in(bot_token, api_hash).await?;
    Ok(Arc::new(CachedClient {
        client: Arc::new(client),
        runner: runner_task,
    }))
}

async fn build_client(
    api_id: i32,
    api_hash: &str,
    session_data: grammers_session::SessionData,
) -> Result<Arc<CachedClient>, Box<dyn std::error::Error + Send + Sync>> {
    let session = Arc::new(MemorySession::from(session_data));
    let pool = SenderPool::new(Arc::clone(&session) as Arc<_>, api_id);
    let runner = pool.runner;
    let handle = pool.handle;
    let runner_task = tokio::spawn(async move {
        runner.run().await;
    });
    let client = Client::new(handle);
    let _ = api_hash;
    Ok(Arc::new(CachedClient {
        client: Arc::new(client),
        runner: runner_task,
    }))
}
