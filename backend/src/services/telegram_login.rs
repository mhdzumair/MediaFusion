//! Redis-backed pending Telegram MTProto login state (phone/code/2FA).
//!
//! Pending login must survive load-balancer hops between API pods; an in-memory
//! map breaks multi-pod deployments because each step may hit a different instance.

use std::sync::Arc;

use base64::{Engine, engine::general_purpose::STANDARD as BASE64};
use fred::prelude::{Expiration, KeysInterface};
use grammers_client::InvocationError;
use grammers_client::client::PasswordToken;
use grammers_client::sender::{SenderPool, SenderPoolFatHandle};
use grammers_client::{Client, SignInError};
use grammers_session::types::UpdatesState;
use grammers_session::{Session, SessionData, storages::MemorySession};
use grammers_tl_types as tl;
use grammers_tl_types::{Deserializable, Serializable};
use serde::{Deserialize, Serialize};
use tokio::task::JoinHandle;

use crate::{
    config::AppConfig,
    crypto::telegram_session as session_crypto,
    db::{self, types::UserId},
    util::telegram_session,
};

const PENDING_LOGIN_TTL_SECS: i64 = 15 * 60;

fn pending_login_key(user_id: i32) -> String {
    format!("telegram:pending_login:{user_id}")
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum PendingStep {
    AwaitingCode,
    AwaitingPassword,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SessionSnapshot {
    home_dc: i32,
    dc_options: Vec<grammers_session::types::DcOption>,
    updates_state: UpdatesState,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RedisPendingLogin {
    step: PendingStep,
    phone: String,
    phone_code_hash: String,
    session: SessionSnapshot,
    password_blob: Option<String>,
}

pub struct PendingLoginStore {
    redis: fred::clients::Client,
}

impl PendingLoginStore {
    pub fn new(redis: fred::clients::Client) -> Self {
        Self { redis }
    }

    pub async fn clear(&self, user_id: UserId) {
        let _: Result<i64, _> = self.redis.del(&pending_login_key(user_id.0)).await;
    }
}

pub enum LoginStartResult {
    CodeSent { phone: String },
}

pub enum LoginVerifyResult {
    Completed { telegram_account_id: i64 },
    PasswordRequired { hint: Option<String> },
}

pub enum LoginPasswordResult {
    Completed { telegram_account_id: i64 },
}

fn require_api(config: &AppConfig) -> Result<(i32, &str), String> {
    let api_id = config.telegram_api_id.ok_or_else(|| {
        "Telegram API credentials are not configured on this instance".to_string()
    })?;
    let api_hash = config.telegram_api_hash.as_deref().ok_or_else(|| {
        "Telegram API credentials are not configured on this instance".to_string()
    })?;
    Ok((api_id, api_hash))
}

async fn request_code_hash(
    ephemeral: &EphemeralClient,
    phone: &str,
    api_hash: &str,
    api_id: i32,
) -> Result<String, String> {
    use tl::enums::auth::SentCode as SC;

    let client = ephemeral.client();
    let request = tl::functions::auth::SendCode {
        phone_number: phone.to_string(),
        api_id,
        api_hash: api_hash.to_string(),
        settings: tl::types::CodeSettings {
            allow_flashcall: false,
            current_number: false,
            allow_app_hash: false,
            allow_missed_call: false,
            allow_firebase: false,
            logout_tokens: None,
            token: None,
            app_sandbox: None,
            unknown_number: false,
        }
        .into(),
    };

    let sent_code = match client.invoke(&request).await {
        Ok(x) => match x {
            SC::Code(code) => code,
            SC::Success(_) => return Err("unexpected login success before code entry".into()),
            SC::PaymentRequired(_) => return Err("telegram payment required for login".into()),
        },
        Err(InvocationError::Rpc(err)) if err.code == 303 => {
            let old_dc_id = ephemeral
                .session()
                .home_dc_id()
                .map_err(|e| format!("session home_dc: {e}"))?;
            let new_dc_id = err
                .value
                .ok_or_else(|| "PHONE_MIGRATE missing dc id".to_string())?
                as i32;
            ephemeral.handle().disconnect_from_dc(old_dc_id);
            ephemeral
                .session()
                .set_home_dc_id(new_dc_id)
                .await
                .map_err(|e| format!("session migrate dc: {e}"))?;
            match client.invoke(&request).await {
                Ok(x) => match x {
                    SC::Code(code) => code,
                    SC::Success(_) => {
                        return Err("unexpected login success before code entry".into());
                    }
                    SC::PaymentRequired(_) => {
                        return Err("telegram payment required for login".into());
                    }
                },
                Err(e) => return Err(format!("request login code after migrate: {e}")),
            }
        }
        Err(e) => return Err(format!("request login code: {e}")),
    };

    Ok(sent_code.phone_code_hash)
}

async fn sign_in_with_hash(
    client: &Client,
    phone: &str,
    phone_code_hash: &str,
    code: &str,
) -> Result<grammers_client::peer::User, SignInError> {
    match client
        .invoke(&tl::functions::auth::SignIn {
            phone_number: phone.to_string(),
            phone_code_hash: phone_code_hash.to_string(),
            phone_code: Some(code.to_string()),
            email_verification: None,
        })
        .await
    {
        Ok(tl::enums::auth::Authorization::Authorization(x)) => {
            Ok(grammers_client::peer::User::from_raw(client, x.user))
        }
        Ok(tl::enums::auth::Authorization::SignUpRequired(_)) => Err(SignInError::SignUpRequired),
        Err(err) if err.is("SESSION_PASSWORD_NEEDED") => {
            let password: tl::types::account::Password =
                match client.invoke(&tl::functions::account::GetPassword {}).await {
                    Ok(value) => value.into(),
                    Err(error) => return Err(SignInError::Other(error)),
                };
            Err(SignInError::PasswordRequired(PasswordToken::new(password)))
        }
        Err(err) if err.is("PHONE_CODE_*") => Err(SignInError::InvalidCode),
        Err(error) => Err(SignInError::Other(error)),
    }
}

async fn fetch_password_data(client: &Client) -> Result<tl::types::account::Password, String> {
    client
        .invoke(&tl::functions::account::GetPassword {})
        .await
        .map_err(|e| format!("fetch password info: {e}"))
        .map(Into::into)
}

async fn spawn_client(api_id: i32, session_data: SessionData) -> EphemeralClient {
    let session = Arc::new(MemorySession::from(session_data));
    let pool = SenderPool::new(Arc::clone(&session) as Arc<_>, api_id);
    let handle = pool.handle.clone();
    let runner = pool.runner;
    let runner_task = tokio::spawn(async move {
        runner.run().await;
    });
    EphemeralClient {
        session,
        handle,
        client: Arc::new(Client::new(pool.handle)),
        runner: runner_task,
    }
}

/// Short-lived MTProto client for login steps — always disconnects on drop so retries
/// cannot leave duplicate auth-key connections open against Telegram.
struct EphemeralClient {
    session: Arc<MemorySession>,
    handle: SenderPoolFatHandle,
    client: Arc<Client>,
    runner: JoinHandle<()>,
}

impl EphemeralClient {
    fn session(&self) -> &MemorySession {
        &self.session
    }

    fn handle(&self) -> &SenderPoolFatHandle {
        &self.handle
    }

    fn client(&self) -> &Client {
        &self.client
    }

    fn shutdown(&mut self) {
        self.client.disconnect();
        self.runner.abort();
    }
}

impl Drop for EphemeralClient {
    fn drop(&mut self) {
        self.shutdown();
    }
}

async fn snapshot_session(session: &MemorySession) -> Result<SessionSnapshot, String> {
    let home_dc = session
        .home_dc_id()
        .map_err(|e| format!("session home_dc: {e}"))?;
    let updates_state = session
        .updates_state()
        .await
        .map_err(|e| format!("session updates_state: {e}"))?;
    let mut dc_options = Vec::new();
    for dc_id in 1..=5 {
        if let Ok(Some(opt)) = session.dc_option(dc_id)
            && opt.auth_key.is_some()
        {
            dc_options.push(opt);
        }
    }
    Ok(SessionSnapshot {
        home_dc,
        dc_options,
        updates_state,
    })
}

fn restore_session_data(snapshot: &SessionSnapshot) -> SessionData {
    let mut data = SessionData {
        home_dc: snapshot.home_dc,
        updates_state: snapshot.updates_state.clone(),
        ..Default::default()
    };
    for opt in &snapshot.dc_options {
        data.dc_options.insert(opt.id, opt.clone());
    }
    data
}

fn serialize_password(password: &tl::types::account::Password) -> Result<String, String> {
    let bytes = tl::enums::account::Password::Password(password.clone()).to_bytes();
    Ok(BASE64.encode(bytes))
}

fn deserialize_password(blob: &str) -> Result<tl::types::account::Password, String> {
    let bytes = BASE64
        .decode(blob.trim())
        .map_err(|e| format!("password blob decode: {e}"))?;
    match tl::enums::account::Password::from_bytes(&bytes)
        .map_err(|e| format!("password blob parse: {e}"))?
    {
        tl::enums::account::Password::Password(password) => Ok(password),
    }
}

async fn save_pending(
    redis: &fred::clients::Client,
    user_id: UserId,
    pending: &RedisPendingLogin,
) -> Result<(), String> {
    let json =
        serde_json::to_string(pending).map_err(|e| format!("serialize pending login: {e}"))?;
    redis
        .set::<(), _, _>(
            &pending_login_key(user_id.0),
            json,
            Some(Expiration::EX(PENDING_LOGIN_TTL_SECS)),
            None,
            false,
        )
        .await
        .map_err(|e| format!("redis save pending login: {e}"))
}

async fn load_pending(
    redis: &fred::clients::Client,
    user_id: UserId,
) -> Result<Option<RedisPendingLogin>, String> {
    let raw: Option<String> = redis
        .get(&pending_login_key(user_id.0))
        .await
        .map_err(|e| format!("redis load pending login: {e}"))?;
    raw.map(|json| serde_json::from_str(&json).map_err(|e| format!("parse pending login: {e}")))
        .transpose()
}

async fn spawn_from_pending(
    config: &AppConfig,
    pending: &RedisPendingLogin,
) -> Result<EphemeralClient, String> {
    let (api_id, _) = require_api(config)?;
    let session_data = restore_session_data(&pending.session);
    Ok(spawn_client(api_id, session_data).await)
}

async fn export_session_data(
    session: &MemorySession,
) -> Result<grammers_session::SessionData, String> {
    Ok(restore_session_data(&snapshot_session(session).await?))
}

async fn persist_authenticated_session(
    pool: &sqlx::PgPool,
    config: &AppConfig,
    user_id: UserId,
    session: &MemorySession,
    grammers_user: &grammers_client::peer::User,
) -> Result<i64, String> {
    let data = export_session_data(session).await?;
    let telethon = telegram_session::export_telethon_string(&data)?;
    let encrypted = session_crypto::encrypt_session(&telethon, &config.secret_key)
        .ok_or_else(|| "failed to encrypt session".to_string())?;
    let account_id = grammers_user
        .id()
        .bot_api_dialog_id()
        .ok_or_else(|| "telegram user has no bot API dialog id".to_string())?;
    db::user_telegram_session::upsert_session(pool, user_id, &encrypted, account_id)
        .await
        .map_err(|e| format!("db upsert session: {e}"))?;
    Ok(account_id)
}

pub async fn start_login(
    pending: &PendingLoginStore,
    config: &AppConfig,
    user_id: UserId,
    phone: &str,
) -> Result<LoginStartResult, String> {
    let phone = phone.trim();
    if phone.len() < 8 {
        return Err("enter a valid phone number in international format".into());
    }

    let (api_id, api_hash) = require_api(config)?;
    let ephemeral = spawn_client(api_id, grammers_session::SessionData::default()).await;
    let phone_code_hash = request_code_hash(&ephemeral, phone, api_hash, api_id).await?;

    let snapshot = snapshot_session(ephemeral.session()).await?;
    let state = RedisPendingLogin {
        step: PendingStep::AwaitingCode,
        phone: phone.to_string(),
        phone_code_hash,
        session: snapshot,
        password_blob: None,
    };
    save_pending(&pending.redis, user_id, &state).await?;

    Ok(LoginStartResult::CodeSent {
        phone: phone.to_string(),
    })
}

pub async fn verify_code(
    pending: &PendingLoginStore,
    pool: &sqlx::PgPool,
    config: &AppConfig,
    user_id: UserId,
    code: &str,
) -> Result<LoginVerifyResult, String> {
    let code = code.trim();
    if code.is_empty() {
        return Err("verification code is required".into());
    }

    let Some(state) = load_pending(&pending.redis, user_id).await? else {
        return Err("login session expired — start again".to_string());
    };
    if state.step != PendingStep::AwaitingCode {
        return Err("expected verification code step".into());
    }

    let ephemeral = spawn_from_pending(config, &state).await?;
    match sign_in_with_hash(
        ephemeral.client(),
        &state.phone,
        &state.phone_code_hash,
        code,
    )
    .await
    {
        Ok(user) => {
            pending.clear(user_id).await;
            let account_id =
                persist_authenticated_session(pool, config, user_id, ephemeral.session(), &user)
                    .await?;
            Ok(LoginVerifyResult::Completed {
                telegram_account_id: account_id,
            })
        }
        Err(SignInError::PasswordRequired(password_token)) => {
            let hint = password_token.hint().map(str::to_string);
            let snapshot = snapshot_session(ephemeral.session()).await?;
            let password_data = fetch_password_data(ephemeral.client()).await?;
            let password_blob = serialize_password(&password_data)?;
            let next = RedisPendingLogin {
                step: PendingStep::AwaitingPassword,
                phone: state.phone,
                phone_code_hash: state.phone_code_hash,
                session: snapshot,
                password_blob: Some(password_blob),
            };
            save_pending(&pending.redis, user_id, &next).await?;
            Ok(LoginVerifyResult::PasswordRequired { hint })
        }
        Err(SignInError::InvalidCode) => Err(
            "Telegram rejected the verification code. If you pasted the code into a Telegram \
             chat (including this bot), Telegram blocks login for security — enter the code on \
             the MediaFusion web UI instead (Configure → Telegram)."
                .into(),
        ),
        Err(SignInError::SignUpRequired) => {
            pending.clear(user_id).await;
            Err(
                "this Telegram account is not registered — create it in the official Telegram app first"
                    .into(),
            )
        }
        Err(e) => Err(format!("sign in failed: {e}")),
    }
}

pub async fn verify_password(
    pending: &PendingLoginStore,
    pool: &sqlx::PgPool,
    config: &AppConfig,
    user_id: UserId,
    password: &str,
) -> Result<LoginPasswordResult, String> {
    let password = password.trim();
    if password.is_empty() {
        return Err("password is required".into());
    }

    let Some(state) = load_pending(&pending.redis, user_id).await? else {
        return Err("login session expired — start again".to_string());
    };
    if state.step != PendingStep::AwaitingPassword {
        return Err("expected 2FA password step".into());
    }
    let password_blob = state
        .password_blob
        .as_deref()
        .ok_or_else(|| "expected 2FA password step".to_string())?;

    let password_data = deserialize_password(password_blob)?;
    let password_token = PasswordToken::new(password_data);

    let ephemeral = spawn_from_pending(config, &state).await?;
    match ephemeral
        .client()
        .check_password(password_token, password)
        .await
    {
        Ok(user) => {
            pending.clear(user_id).await;
            let account_id =
                persist_authenticated_session(pool, config, user_id, ephemeral.session(), &user)
                    .await?;
            Ok(LoginPasswordResult::Completed {
                telegram_account_id: account_id,
            })
        }
        Err(SignInError::InvalidPassword(_)) => {
            let snapshot = snapshot_session(ephemeral.session()).await?;
            let password_data = fetch_password_data(ephemeral.client()).await?;
            let password_blob = serialize_password(&password_data)?;
            let next = RedisPendingLogin {
                step: PendingStep::AwaitingPassword,
                phone: state.phone,
                phone_code_hash: state.phone_code_hash,
                session: snapshot,
                password_blob: Some(password_blob),
            };
            save_pending(&pending.redis, user_id, &next).await?;
            Err("invalid 2FA password".into())
        }
        Err(e) => Err(format!("2FA sign in failed: {e}")),
    }
}

pub async fn delete_user_session(
    pool: &sqlx::PgPool,
    pending: &PendingLoginStore,
    clients: &crate::scrapers::telegram_clients::TelegramClientPool,
    user_id: UserId,
) -> Result<bool, String> {
    pending.clear(user_id).await;
    let _ = clients
        .with_user_client(pool, user_id, |client| async move {
            let _ = client.sign_out().await;
        })
        .await;
    clients.invalidate(user_id).await;
    crate::services::telegram_peer::invalidate_dialog_peer_cache(user_id).await;
    db::user_telegram_session::delete_session(pool, user_id)
        .await
        .map_err(|e| format!("delete session: {e}"))
}
