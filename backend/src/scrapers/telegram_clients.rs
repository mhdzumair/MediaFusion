//! Per-user Telegram MTProto client pool.

use std::sync::Arc;
use std::time::Duration;

use grammers_client::Client;
use grammers_client::sender::SenderPool;
use grammers_session::storages::MemorySession;
use moka::future::Cache;
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

pub struct TelegramClientPool {
    cache: Cache<UserId, Arc<CachedClient>>,
    config: AppConfig,
}

impl TelegramClientPool {
    pub fn new(config: AppConfig) -> Self {
        let cache = Cache::builder()
            .max_capacity(64)
            .time_to_live(Duration::from_secs(30 * 60))
            .build();
        Self { cache, config }
    }

    pub fn api_configured(&self) -> bool {
        self.config.telegram_api_id.is_some() && self.config.telegram_api_hash.is_some()
    }

    pub async fn get_client(&self, pool: &sqlx::PgPool, user_id: UserId) -> Option<Arc<Client>> {
        if let Some(entry) = self.cache.get(&user_id).await {
            return Some(Arc::clone(&entry.client));
        }

        let api_id = self.config.telegram_api_id?;
        let api_hash = self.config.telegram_api_hash.as_deref()?;

        let row = db::user_telegram_session::get_session(pool, user_id).await?;
        let session_plain =
            session_crypto::decrypt_session(&row.encrypted_session, &self.config.secret_key)?;
        let session_data = telegram_session::parse_session_data(&session_plain).ok()?;
        if !telegram_session::session_is_authenticated(&session_data) {
            tracing::warn!("telegram: user {user_id} session is not authenticated");
            return None;
        }

        let client = build_client(api_id, api_hash, session_data).await.ok()?;
        self.cache.insert(user_id, client).await;
        db::user_telegram_session::touch_last_used(pool, user_id).await;
        self.cache
            .get(&user_id)
            .await
            .map(|entry| Arc::clone(&entry.client))
    }

    pub async fn invalidate(&self, user_id: UserId) {
        if let Some(entry) = self.cache.get(&user_id).await {
            shutdown_client(&entry.client, &entry.runner);
        }
        self.cache.invalidate(&user_id).await;
    }
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
