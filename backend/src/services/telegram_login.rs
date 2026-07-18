//! In-memory pending Telegram MTProto login state (phone/code/2FA).

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use grammers_client::client::{LoginToken, PasswordToken};
use grammers_client::sender::SenderPool;
use grammers_client::{Client, SignInError};
use grammers_session::Session;
use grammers_session::storages::MemorySession;
use tokio::task::JoinHandle;

use crate::{
    config::AppConfig,
    crypto::telegram_session as session_crypto,
    db::{self, types::UserId},
    util::telegram_session,
};

enum PendingEntry {
    AwaitingCode {
        phone: String,
        login_token: LoginToken,
        session: Arc<MemorySession>,
        client: Arc<Client>,
        _runner: JoinHandle<()>,
    },
    AwaitingPassword {
        session: Arc<MemorySession>,
        client: Arc<Client>,
        password_token: PasswordToken,
        _runner: JoinHandle<()>,
    },
}

pub struct PendingLoginStore {
    entries: Mutex<HashMap<i32, PendingEntry>>,
}

impl PendingLoginStore {
    pub fn new() -> Self {
        Self {
            entries: Mutex::new(HashMap::new()),
        }
    }

    pub fn clear(&self, user_id: UserId) {
        if let Ok(mut map) = self.entries.lock() {
            map.remove(&user_id.0);
        }
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

async fn spawn_client(
    api_id: i32,
    session_data: grammers_session::SessionData,
) -> (Arc<MemorySession>, Arc<Client>, JoinHandle<()>) {
    let session = Arc::new(MemorySession::from(session_data));
    let pool = SenderPool::new(Arc::clone(&session) as Arc<_>, api_id);
    let runner = pool.runner;
    let runner_task = tokio::spawn(async move {
        runner.run().await;
    });
    (
        Arc::clone(&session),
        Arc::new(Client::new(pool.handle)),
        runner_task,
    )
}

async fn export_session_data(
    session: &MemorySession,
) -> Result<grammers_session::SessionData, String> {
    let home_dc = session.home_dc_id();
    let dc = session
        .dc_option(home_dc)
        .ok_or_else(|| format!("session missing DC {home_dc} options"))?;
    let mut data = grammers_session::SessionData {
        home_dc,
        ..Default::default()
    };
    if let Some(opt) = data.dc_options.get_mut(&home_dc) {
        opt.ipv4 = dc.ipv4;
        opt.auth_key = dc.auth_key;
    }
    Ok(data)
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
    let account_id = grammers_user.id().bot_api_dialog_id();
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
    let (session, client, runner) =
        spawn_client(api_id, grammers_session::SessionData::default()).await;
    let login_token = client
        .request_login_code(phone, api_hash)
        .await
        .map_err(|e| format!("request login code: {e}"))?;

    if let Ok(mut map) = pending.entries.lock() {
        map.insert(
            user_id.0,
            PendingEntry::AwaitingCode {
                phone: phone.to_string(),
                login_token,
                session,
                client,
                _runner: runner,
            },
        );
    }

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

    let entry = pending
        .entries
        .lock()
        .ok()
        .and_then(|mut map| map.remove(&user_id.0))
        .ok_or_else(|| "login session expired — start again".to_string())?;

    let PendingEntry::AwaitingCode {
        phone,
        login_token,
        session,
        client,
        _runner,
    } = entry
    else {
        return Err("expected verification code step".into());
    };

    match client.sign_in(&login_token, code).await {
        Ok(user) => {
            let account_id =
                persist_authenticated_session(pool, config, user_id, &session, &user).await?;
            Ok(LoginVerifyResult::Completed { telegram_account_id: account_id })
        }
        Err(SignInError::PasswordRequired(password_token)) => {
            let hint = password_token.hint().map(str::to_string);
            if let Ok(mut map) = pending.entries.lock() {
                map.insert(
                    user_id.0,
                    PendingEntry::AwaitingPassword {
                        session,
                        client,
                        password_token,
                        _runner,
                    },
                );
            }
            Ok(LoginVerifyResult::PasswordRequired { hint })
        }
        Err(SignInError::InvalidCode) => {
            if let Ok(mut map) = pending.entries.lock() {
                map.insert(
                    user_id.0,
                    PendingEntry::AwaitingCode {
                        phone,
                        login_token,
                        session,
                        client,
                        _runner,
                    },
                );
            }
            Err(
                "Telegram rejected the verification code. If you pasted the code into a Telegram \
                 chat (including this bot), Telegram blocks login for security — enter the code on \
                 the MediaFusion web UI instead (Configure → Telegram)."
                    .into(),
            )
        }
        Err(SignInError::SignUpRequired) => Err(
            "this Telegram account is not registered — create it in the official Telegram app first"
                .into(),
        ),
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

    let entry = pending
        .entries
        .lock()
        .ok()
        .and_then(|mut map| map.remove(&user_id.0))
        .ok_or_else(|| "login session expired — start again".to_string())?;

    let PendingEntry::AwaitingPassword {
        session,
        client,
        password_token,
        _runner,
    } = entry
    else {
        return Err("expected 2FA password step".into());
    };

    let user = client
        .check_password(password_token, password)
        .await
        .map_err(|e| match e {
            SignInError::InvalidPassword(_) => "invalid 2FA password".to_string(),
            other => format!("2FA sign in failed: {other}"),
        })?;

    let account_id = persist_authenticated_session(pool, config, user_id, &session, &user).await?;
    Ok(LoginPasswordResult::Completed {
        telegram_account_id: account_id,
    })
}

pub async fn delete_user_session(
    pool: &sqlx::PgPool,
    pending: &PendingLoginStore,
    clients: &crate::scrapers::telegram_clients::TelegramClientPool,
    user_id: UserId,
) -> Result<bool, String> {
    pending.clear(user_id);
    clients.invalidate(user_id).await;
    db::user_telegram_session::delete_session(pool, user_id)
        .await
        .map_err(|e| format!("delete session: {e}"))
}
