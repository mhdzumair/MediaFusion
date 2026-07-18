//! Message templates (legacy Markdown, not MarkdownV2).

pub fn welcome(first_name: Option<&str>, enabled_content: &[&str]) -> String {
    let name = first_name.unwrap_or("there");
    let content_lines = if enabled_content.is_empty() {
        "• All content types are currently disabled on this instance.".to_string()
    } else {
        enabled_content
            .iter()
            .map(|line| format!("• {line}"))
            .collect::<Vec<_>>()
            .join("\n")
    };
    format!(
        "👋 *Welcome, {name}!*\n\n\
         I'm the MediaFusion content bot. Send me content to contribute:\n\n\
         {content_lines}\n\n\
         *Getting started:*\n\
         1. Send /login to link your MediaFusion account\n\
         2. Send /session for Telegram scraping setup (web UI)\n\
         3. Send content to start the import wizard\n\n\
         Type /help for command reference."
    )
}

pub fn help_text(enabled_content: &[&str]) -> String {
    let content_section = if enabled_content.is_empty() {
        "All content types are currently disabled on this instance.".to_string()
    } else {
        format!("Send {}.", enabled_content.join(", "))
    };
    format!(
        "*MediaFusion Bot Commands*\n\n\
         /start — Welcome message\n\
         /help — This help message\n\
         /login — Link your Telegram account to MediaFusion\n\
         /status — Check account link status\n\
         /cancel — Cancel current operation\n\n\
         *Telegram scraping:*\n\
         /session — How to connect your scraping session (web UI)\n\
         /dropsession — Disconnect scraping session\n\
         /browsechannels — Pick a channel from your Telegram account\n\
         /addchannel — Add a channel by @username\n\
         /removechannel — Remove a channel\n\
         /channels — List your configured scraping channels\n\
         /scrape — Scrape configured channels (prompts for message count)\n\
         /scrape 25 — Scrape last 25 messages per channel\n\
         /scrape all — Scrape full channel history\n\
         /scrape 50 @channel — Scrape one channel (last 50 messages)\n\n\
         *Contributing content:*\n\
         {content_section}\n\n\
         You'll be guided through matching content to media and confirming the import."
    )
}

pub fn login_private_chat_required() -> String {
    "🔒 *Private Chat Required*\n\n\
     For security, /login only works in a private chat.\n\n\
     Please open a direct chat with this bot and send /login there."
        .to_string()
}

pub fn login_success(login_url: &str, login_token: &str) -> String {
    format!(
        "🔐 *Link Your MediaFusion Account*\n\n\
         To link your Telegram account to MediaFusion:\n\n\
         1. Click this link: [Login to MediaFusion]({login_url})\n\
         2. Sign in to your MediaFusion account\n\
         3. Your Telegram account will be linked automatically\n\n\
         *Login Token:* {login_token}\n\
         *Expires in:* 24 hours\n\n\
         After linking, your uploaded content will be stored with your MediaFusion account."
    )
}

pub fn status_linked(
    username: &str,
    mf_user_id: i64,
    session_connected: bool,
    channel_count: usize,
) -> String {
    let session_line = if session_connected {
        "Connected"
    } else {
        "Not connected — send /session"
    };
    format!(
        "✅ *Account Status*\n\n\
         *Status:* Linked\n\
         *Username:* {username}\n\
         *MediaFusion ID:* {mf_user_id}\n\
         *Scraping session:* {session_line}\n\
         *Scraping channels:* {channel_count}\n\n\
         You can contribute content by sending:\n\
         • Magnet links\n\
         • Torrent files\n\
         • YouTube URLs\n\
         • HTTP direct links\n\
         • Video files\n\
         • NZB URLs\n\
         • AceStream IDs"
    )
}

pub fn status_not_linked() -> String {
    "❌ *Account Status*\n\n\
     *Status:* Not Linked\n\n\
     Your Telegram account is not linked to MediaFusion.\n\n\
     Send /login to link your account and start contributing content."
        .to_string()
}

pub fn cancel_success() -> String {
    "❌ *Operation Cancelled*\n\nYour current operation has been cancelled.".to_string()
}

pub fn cancel_batch() -> String {
    "❌ *Batch Cancelled*\n\nAll pending imports have been cancelled.".to_string()
}

pub fn cancel_nothing() -> String {
    "ℹ️ *No Active Operation*\n\nThere's nothing to cancel.".to_string()
}

pub fn session_web_instructions(host_url: &str) -> String {
    let integrations_url = format!(
        "{}/app/dashboard/integrations",
        host_url.trim_end_matches('/')
    );
    format!(
        "🔐 *Telegram Scraping Session*\n\n\
         Session login is done on the MediaFusion web UI only (not in this bot).\n\n\
         *Why:* Telegram blocks login codes sent in any chat, including bot DMs.\n\n\
         *Steps:*\n\
         1. Open [Integrations → Telegram]({integrations_url}) in your browser\n\
         2. Read the warning and connect your Telegram account\n\
         3. Enter the verification code and 2FA password on the web UI\n\n\
         *Recommendations:*\n\
         • Use a dedicated/non-personal Telegram account when possible\n\
         • The developer and instance hoster are not responsible for any account loss\n\n\
         After connecting, you'll get a confirmation message here.\n\
         Then use /browsechannels or /addchannel to configure scraping."
    )
}

pub fn session_connected_notification(account_id: i64) -> String {
    format!(
        "✅ *Scraping Session Connected*\n\n\
         Telegram account {account_id} is now linked for channel scraping.\n\n\
         Next steps:\n\
         • /browsechannels — pick public or private channels/groups from your chats\n\
         • /addchannel — add by @username or id:-100…\n\
         • /scrape — start scraping"
    )
}

pub fn unauthorized() -> String {
    "❌ Unauthorized".to_string()
}

pub fn content_preview(content_type: &str, preview: &str) -> String {
    let escaped_type = escape_markdown(content_type);
    format!(
        "📥 *Content Detected*\n\n*Type:* {escaped_type}\n*Preview:* {preview}\n\nSelect the media type:"
    )
}

pub fn escape_markdown(text: &str) -> String {
    text.replace('_', "\\_")
        .replace('*', "\\*")
        .replace('`', "'")
}

pub fn bytes_readable(size: i64) -> String {
    const UNITS: &[&str] = &["B", "KB", "MB", "GB", "TB"];
    if size <= 0 {
        return "Unknown".to_string();
    }
    let mut val = size as f64;
    let mut unit = 0;
    while val >= 1024.0 && unit < UNITS.len() - 1 {
        val /= 1024.0;
        unit += 1;
    }
    if unit == 0 {
        format!("{size} B")
    } else {
        format!("{val:.1} {}", UNITS[unit])
    }
}
