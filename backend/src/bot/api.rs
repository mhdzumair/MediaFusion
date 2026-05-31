//! Raw Telegram Bot HTTP API client.

use reqwest::Client;
use serde_json::{json, Value};

use crate::state::AppState;

pub struct BotApi {
    http: Client,
    base_url: String,
}

#[derive(Debug)]
pub enum BotApiError {
    NotConfigured,
    Http(reqwest::Error),
    Api(String),
    Parse(String),
}

impl std::fmt::Display for BotApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NotConfigured => write!(f, "Telegram bot not configured"),
            Self::Http(e) => write!(f, "HTTP error: {e}"),
            Self::Api(m) => write!(f, "Telegram API error: {m}"),
            Self::Parse(m) => write!(f, "Parse error: {m}"),
        }
    }
}

impl std::error::Error for BotApiError {}

impl BotApi {
    pub fn from_state(state: &AppState) -> Result<Self, BotApiError> {
        let token = state
            .config
            .telegram_bot_token
            .as_deref()
            .ok_or(BotApiError::NotConfigured)?;
        Ok(Self {
            http: state.http.clone(),
            base_url: format!("https://api.telegram.org/bot{token}"),
        })
    }

    async fn post(&self, method: &str, body: Value) -> Result<Value, BotApiError> {
        let url = format!("{}/{}", self.base_url, method);
        let resp = self
            .http
            .post(&url)
            .json(&body)
            .send()
            .await
            .map_err(BotApiError::Http)?;
        let data: Value = resp.json().await.map_err(BotApiError::Http)?;
        if data.get("ok").and_then(|v| v.as_bool()) != Some(true) {
            let desc = data
                .get("description")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown error");
            return Err(BotApiError::Api(desc.to_string()));
        }
        Ok(data)
    }

    pub async fn get_me(&self) -> Result<Value, BotApiError> {
        self.post("getMe", json!({})).await
    }

    pub async fn set_my_commands(&self, commands: &[(&str, &str)]) -> Result<(), BotApiError> {
        let cmds: Vec<Value> = commands
            .iter()
            .map(|(command, description)| {
                json!({
                    "command": command,
                    "description": description,
                })
            })
            .collect();
        self.post("setMyCommands", json!({ "commands": cmds }))
            .await?;
        Ok(())
    }

    pub async fn send_message(
        &self,
        chat_id: i64,
        text: &str,
        reply_markup: Option<Value>,
    ) -> Result<i64, BotApiError> {
        let mut body = json!({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": true,
        });
        if let Some(rm) = reply_markup {
            body["reply_markup"] = rm;
        }
        let data = self.post("sendMessage", body).await?;
        data.get("result")
            .and_then(|r| r.get("message_id"))
            .and_then(|v| v.as_i64())
            .ok_or_else(|| BotApiError::Parse("missing message_id".into()))
    }

    pub async fn edit_message_text(
        &self,
        chat_id: i64,
        message_id: i64,
        text: &str,
        reply_markup: Option<Value>,
    ) -> Result<(), BotApiError> {
        let mut body = json!({
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": true,
        });
        if let Some(rm) = reply_markup {
            body["reply_markup"] = rm;
        }
        let _ = self.post("editMessageText", body).await?;
        Ok(())
    }

    pub async fn answer_callback_query(
        &self,
        callback_query_id: &str,
        text: Option<&str>,
        show_alert: bool,
    ) -> Result<(), BotApiError> {
        let mut body = json!({
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        });
        if let Some(t) = text {
            body["text"] = json!(t);
        }
        let _ = self.post("answerCallbackQuery", body).await?;
        Ok(())
    }

    pub async fn get_file(&self, file_id: &str) -> Result<Value, BotApiError> {
        let data = self.post("getFile", json!({ "file_id": file_id })).await?;
        data.get("result")
            .cloned()
            .ok_or_else(|| BotApiError::Parse("missing result".into()))
    }

    pub async fn download_file(&self, file_path: &str) -> Result<Vec<u8>, BotApiError> {
        let token = self
            .base_url
            .strip_prefix("https://api.telegram.org/bot")
            .unwrap_or("");
        let url = format!("https://api.telegram.org/file/bot{token}/{file_path}");
        self.http
            .get(&url)
            .send()
            .await
            .map_err(BotApiError::Http)?
            .bytes()
            .await
            .map(|b| b.to_vec())
            .map_err(BotApiError::Http)
    }

    pub async fn send_video(
        &self,
        chat_id: &str,
        file_id: &str,
        caption: Option<&str>,
    ) -> Result<Value, BotApiError> {
        let mut body = json!({
            "chat_id": chat_id,
            "video": file_id,
        });
        if let Some(c) = caption {
            body["caption"] = json!(c);
            body["parse_mode"] = json!("Markdown");
        }
        let data = self.post("sendVideo", body).await?;
        data.get("result")
            .cloned()
            .ok_or_else(|| BotApiError::Parse("missing result".into()))
    }

    pub async fn copy_message(
        &self,
        chat_id: i64,
        from_chat_id: i64,
        message_id: i64,
    ) -> Result<Value, BotApiError> {
        let data = self
            .post(
                "copyMessage",
                json!({
                    "chat_id": chat_id,
                    "from_chat_id": from_chat_id,
                    "message_id": message_id,
                }),
            )
            .await?;
        data.get("result")
            .cloned()
            .ok_or_else(|| BotApiError::Parse("missing result".into()))
    }
}
