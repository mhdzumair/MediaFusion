//! Telegram notification handlers registered at startup.

use std::sync::Arc;

use crate::{state::AppState, util::notification_registry};

/// Wire Telegram bot notifications into the shared notification registry.
pub fn register_notification_handlers(state: Arc<AppState>) {
    let bot_token = match state.config.telegram_bot_token.clone() {
        Some(t) if !t.is_empty() => t,
        _ => return,
    };
    let chat_id = match state.config.telegram_chat_id.clone() {
        Some(c) if !c.is_empty() => c,
        _ => return,
    };

    let host_url = state.config.host_url.clone();
    let http = state.http.clone();

    notification_registry::register_file_annotation_handler(Arc::new(
        move |info_hash, torrent_name| {
            let bot_token = bot_token.clone();
            let chat_id = chat_id.clone();
            let host_url = host_url.clone();
            let http = http.clone();
            Box::pin(async move {
                send_file_annotation_telegram(
                    &http,
                    &bot_token,
                    &chat_id,
                    &host_url,
                    &info_hash,
                    &torrent_name,
                )
                .await;
            })
        },
    ));
}

pub async fn send_block_notification(
    http: &reqwest::Client,
    bot_token: &str,
    chat_id: &str,
    info_hash: &str,
    action: &str,
    meta_id: &str,
    title: &str,
    meta_type: &str,
    poster: &str,
    torrent_name: &str,
) {
    let meta_id_data = if meta_id.starts_with("tt") {
        format!("*IMDb*: [{meta_id}](https://www.imdb.com/title/{meta_id}/)\n")
    } else {
        format!("Meta ID: {meta_id}\n")
    };
    let action_label = if action == "block" {
        "Blocked"
    } else {
        "Deleted"
    };
    let message = format!(
        "🚫 Torrent {action_label}\n\n\
         *Title*: {title}\n\
         *Type*: {}\n\
         {meta_id_data}\
         *Torrent Name*: `{torrent_name}`\n\
         *Info Hash*: `{info_hash}`\n\
         *Poster*: [View]({poster})",
        meta_type.to_ascii_uppercase()
    );
    send_photo_message(http, bot_token, chat_id, poster, &message).await;
}

pub async fn send_migration_notification(
    http: &reqwest::Client,
    bot_token: &str,
    chat_id: &str,
    old_id: &str,
    new_id: &str,
    title: &str,
    meta_type: &str,
    poster: &str,
) {
    let message = format!(
        "🔄 ID Migration Complete\n\n\
         *Title*: {title}\n\
         *Type*: {}\n\
         *Old ID*: `{old_id}`\n\
         *New IMDb ID*: [{new_id}](https://www.imdb.com/title/{new_id}/)\n\
         *Poster*: [View]({poster})",
        meta_type.to_ascii_uppercase()
    );
    send_photo_message(http, bot_token, chat_id, poster, &message).await;
}

pub async fn send_image_update_notification(
    http: &reqwest::Client,
    bot_token: &str,
    chat_id: &str,
    meta_id: &str,
    title: &str,
    meta_type: &str,
    poster: &str,
    old_poster: Option<&str>,
    old_background: Option<&str>,
    old_logo: Option<&str>,
    new_poster: Option<&str>,
    new_background: Option<&str>,
    new_logo: Option<&str>,
) {
    let meta_id_data = if meta_id.starts_with("tt") {
        format!("*IMDb*: [{meta_id}](https://www.imdb.com/title/{meta_id}/)\n")
    } else {
        format!("Meta ID: {meta_id}\n")
    };
    let mut message = format!(
        "🖼️ Images Updated\n\n\
         *Title*: {title}\n\
         *Type*: {}\n\
         {meta_id_data}",
        meta_type.to_ascii_uppercase()
    );
    if let Some(url) = old_poster {
        message.push_str(&format!("*Old Poster*: [View]({url})\n"));
    }
    if let Some(url) = old_background {
        message.push_str(&format!("*Old Background*: [View]({url})\n"));
    }
    if let Some(url) = old_logo {
        message.push_str(&format!("*Old Logo*: [View]({url})\n"));
    }
    if new_poster.is_some() || new_background.is_some() || new_logo.is_some() {
        message.push_str("\n*Updated*:\n");
        if let Some(url) = new_poster {
            message.push_str(&format!("• Poster: [View]({url})\n"));
        }
        if let Some(url) = new_background {
            message.push_str(&format!("• Background: [View]({url})\n"));
        }
        if let Some(url) = new_logo {
            message.push_str(&format!("• Logo: [View]({url})\n"));
        }
    }
    message.push_str(&format!("*Poster*: [View]({poster})"));
    send_photo_message(http, bot_token, chat_id, poster, &message).await;
}

pub async fn send_content_received_notification(
    http: &reqwest::Client,
    bot_token: &str,
    chat_id: &str,
    file_name: &str,
    file_size: i64,
    status: &str,
    meta_id: Option<&str>,
    title: Option<&str>,
    error_message: Option<&str>,
) {
    let size_str = if file_size >= 1024 * 1024 * 1024 {
        format!("{:.2} GB", file_size as f64 / (1024.0 * 1024.0 * 1024.0))
    } else if file_size >= 1024 * 1024 {
        format!("{:.2} MB", file_size as f64 / (1024.0 * 1024.0))
    } else {
        format!("{:.2} KB", file_size as f64 / 1024.0)
    };
    let status_emoji = match status {
        "processing" => "⏳",
        "stored" => "✅",
        "failed" => "❌",
        _ => "📥",
    };
    let mut message = format!(
        "{status_emoji} *Content {status}*\n\n\
         *File*: `{file_name}`\n\
         *Size*: {size_str}\n"
    );
    if let Some(id) = meta_id.filter(|s| !s.is_empty()) {
        message.push_str(&format!("*Meta ID*: `{id}`\n"));
    }
    if let Some(t) = title.filter(|s| !s.is_empty()) {
        message.push_str(&format!("*Title*: {t}\n"));
    }
    if let Some(err) = error_message.filter(|s| !s.is_empty()) {
        message.push_str(&format!("*Error*: `{err}`\n"));
    }
    send_text_message(http, bot_token, chat_id, &message).await;
}

async fn send_file_annotation_telegram(
    http: &reqwest::Client,
    bot_token: &str,
    chat_id: &str,
    host_url: &str,
    info_hash: &str,
    torrent_name: &str,
) {
    let annotation_url = format!(
        "{}/app/dashboard/moderator?tab=annotation",
        host_url.trim_end_matches('/')
    );
    let message = format!(
        "📝 Episode file mapping required\n\n\
         *Info Hash*: `{info_hash}`\n\
         *Torrent Name*: `{torrent_name}`\n\
         *Annotation Queue*: [Open]({annotation_url})\n\
         Please review and annotate the episode mappings manually."
    );
    send_text_message(http, bot_token, chat_id, &message).await;
}

async fn send_text_message(http: &reqwest::Client, bot_token: &str, chat_id: &str, message: &str) {
    let url = format!("https://api.telegram.org/bot{bot_token}/sendMessage");
    let payload = serde_json::json!({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": true,
    });
    if let Err(e) = http.post(&url).json(&payload).send().await {
        tracing::warn!("telegram notification sendMessage failed: {e}");
    }
}

async fn send_photo_message(
    http: &reqwest::Client,
    bot_token: &str,
    chat_id: &str,
    photo_url: &str,
    caption: &str,
) {
    let url = format!("https://api.telegram.org/bot{bot_token}/sendPhoto");
    let payload = serde_json::json!({
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "Markdown",
    });
    if let Err(e) = http.post(&url).json(&payload).send().await {
        tracing::warn!("telegram notification sendPhoto failed: {e}, falling back to text");
        send_text_message(http, bot_token, chat_id, caption).await;
    }
}

/// Fire-and-forget admin notification when credentials are configured.
pub fn notify_if_enabled(state: &AppState, fut: impl std::future::Future<Output = ()> + Send + 'static) {
    if state.config.telegram_bot_token.is_some() && state.config.telegram_chat_id.is_some() {
        tokio::spawn(fut);
    }
}
