/// Application configuration loaded from environment variables.
#[derive(Clone)]
pub struct AppConfig {
    /// 32-byte padded key for AES-256-CBC stream decryption.
    pub secret_key: [u8; 32],
    /// Raw secret key string (used for HMAC-SHA256 manifest cache keys).
    pub secret_key_raw: String,
    pub postgres_uri: String,
    pub postgres_ro_uri: Option<String>,
    /// Redis URL for the Rust service — defaults to DB 1 to avoid colliding with Python's DB 0.
    /// Override with REDIS_RS_URL; falls back to REDIS_URL/1 if only REDIS_URL is set.
    pub redis_url: String,
    pub port: u16,
    pub meta_cache_ttl: u64,
    pub catalog_cache_ttl: u64,
    /// Absolute base URL of this service (e.g. "https://mediafusion.example.com").
    /// Used to build poster URLs in meta responses.
    pub host_url: String,
    /// Base URL of the Python MediaFusion instance for fallback/proxy calls.
    pub python_proxy_url: Option<String>,
    // Addon identity fields (shown in Stremio manifest).
    pub addon_name: String,
    pub addon_version: String,
    pub addon_description: String,
    pub logo_url: String,
    pub contact_email: Option<String>,

    // ── Scraper endpoints ────────────────────────────────────────────────────
    pub prowlarr_url: Option<String>,
    pub prowlarr_api_key: Option<String>,
    pub torrentio_url: String,
    pub zilean_url: Option<String>,
    pub jackett_url: Option<String>,
    pub jackett_api_key: Option<String>,
    pub mediafusion_url: Option<String>,
    pub live_search_streams: bool,

    // ── Torznab / auth ───────────────────────────────────────────────────────
    /// Optional API password for Torznab and private-instance validation.
    pub api_password: Option<String>,
    /// Enable the Torznab feed endpoint (default: true).
    pub enable_torznab_api: bool,

    // ── Easynews credentials ─────────────────────────────────────────────────
    pub easynews_username: Option<String>,
    pub easynews_password: Option<String>,

    // ── TorBox search TTL ────────────────────────────────────────────────────
    pub torbox_search_ttl: u64,

    // ── Telegram bot ────────────────────────────────────────────────────────
    pub telegram_bot_token: Option<String>,
    pub telegram_bot_username: Option<String>,
    pub telegram_webhook_secret_token: Option<String>,

    // ── Auth ────────────────────────────────────────────────────────────────
    /// SMTP host for sending verification/reset emails (optional).
    pub smtp_host: Option<String>,
    pub smtp_port: u16,
    pub smtp_username: Option<String>,
    pub smtp_password: Option<String>,
    pub smtp_from: String,

    // ── Debrid cache sync ───────────────────────────────────────────────────
    /// Whether to sync debrid cache to a central MediaFusion instance.
    pub sync_debrid_cache_streams: bool,

    // ── Telegram MTProto (grammers) ─────────────────────────────────────────
    /// Telegram API ID (from https://my.telegram.org).
    pub telegram_api_id: Option<i32>,
    /// Telegram API hash.
    pub telegram_api_hash: Option<String>,
    /// Base64-encoded grammers session bytes (separate from Telethon session).
    pub telegram_grammers_session: Option<String>,
    /// List of channel @usernames to scrape for media files.
    pub telegram_scraping_channels: Vec<String>,
    /// Maximum number of messages to fetch per channel during live scrape.
    pub telegram_scrape_message_limit: i32,
    /// Minimum video file size in bytes to consider (default 50 MB).
    pub min_scraping_video_size: u64,

    // ── IPTV import ─────────────────────────────────────────────────────────
    pub enable_iptv_import: bool,
    pub allow_public_iptv_sharing: bool,
}

impl AppConfig {
    pub fn from_env() -> Self {
        // Walk up from cwd until we find a .env file (handles running from
        // services/api/ or repo root interchangeably).
        if let Ok(cwd) = std::env::current_dir() {
            let mut dir = Some(cwd.as_path());
            while let Some(d) = dir {
                if d.join(".env").exists() {
                    let _ = dotenvy::from_path(d.join(".env"));
                    break;
                }
                dir = d.parent();
            }
        }

        let raw = std::env::var("SECRET_KEY").expect("SECRET_KEY required");
        let mut key = [b' '; 32];
        let b = raw.as_bytes();
        key[..b.len().min(32)].copy_from_slice(&b[..b.len().min(32)]);

        let contact_email = std::env::var("CONTACT_EMAIL")
            .ok()
            .filter(|e| !e.is_empty() && e != "admin@example.com");

        AppConfig {
            secret_key: key,
            secret_key_raw: raw,
            postgres_uri: std::env::var("POSTGRES_URI").unwrap_or_else(|_| {
                "postgresql://mediafusion:mediafusion@127.0.0.1:5432/mediafusion".into()
            }),
            postgres_ro_uri: std::env::var("POSTGRES_RO_URI").ok(),
            redis_url: std::env::var("REDIS_RS_URL").unwrap_or_else(|_| {
                // Fall back to REDIS_URL but switch to DB 1 to avoid colliding with Python on DB 0.
                let base = std::env::var("REDIS_URL")
                    .unwrap_or_else(|_| "redis://127.0.0.1:6379".into());
                let base = base.trim_end_matches('/');
                // If a DB is already specified (e.g. redis://host/2), keep it; otherwise append /1.
                if base.contains("://") && base[base.find("://").unwrap() + 3..].contains('/') {
                    base.to_string()
                } else {
                    format!("{base}/1")
                }
            }),
            port: std::env::var("STREAM_RS_PORT")
                .unwrap_or_else(|_| "8000".into())
                .parse()
                .unwrap_or(8000),
            meta_cache_ttl: std::env::var("META_CACHE_TTL_SECONDS")
                .unwrap_or_else(|_| "1800".into())
                .parse()
                .unwrap_or(1800),
            catalog_cache_ttl: std::env::var("CATALOG_CACHE_TTL_SECONDS")
                .unwrap_or_else(|_| "1800".into())
                .parse()
                .unwrap_or(1800),
            host_url: std::env::var("HOST_URL")
                .unwrap_or_else(|_| "http://localhost:8000".into())
                .trim_end_matches('/')
                .to_string(),
            python_proxy_url: std::env::var("PYTHON_BASE_URL")
                .ok()
                .map(|u| u.trim_end_matches('/').to_string()),
            addon_name: std::env::var("ADDON_NAME")
                .unwrap_or_else(|_| "MediaFusion".into()),
            addon_version: std::env::var("ADDON_VERSION")
                .unwrap_or_else(|_| "1.0.0".into()),
            addon_description: std::env::var("ADDON_DESCRIPTION").unwrap_or_else(|_| {
                "MediaFusion — universal torrent & debrid streaming addon for Stremio".into()
            }),
            logo_url: std::env::var("ADDON_LOGO").unwrap_or_else(|_| {
                "https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/images/mediafusion_logo.png".into()
            }),
            contact_email,
            prowlarr_url: std::env::var("PROWLARR_URL").ok().map(|u| u.trim_end_matches('/').to_string()),
            prowlarr_api_key: std::env::var("PROWLARR_API_KEY").ok().filter(|s| !s.is_empty()),
            torrentio_url: std::env::var("TORRENTIO_URL")
                .unwrap_or_else(|_| "https://torrentio.strem.fun".into())
                .trim_end_matches('/')
                .to_string(),
            zilean_url: std::env::var("ZILEAN_URL").ok().map(|u| u.trim_end_matches('/').to_string()),
            jackett_url: std::env::var("JACKETT_URL").ok().map(|u| u.trim_end_matches('/').to_string()),
            jackett_api_key: std::env::var("JACKETT_API_KEY").ok().filter(|s| !s.is_empty()),
            mediafusion_url: std::env::var("MEDIAFUSION_URL").ok().map(|u| u.trim_end_matches('/').to_string()),
            live_search_streams: std::env::var("LIVE_SEARCH_STREAMS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(false),
            api_password: std::env::var("API_PASSWORD").ok().filter(|s| !s.is_empty()),
            enable_torznab_api: std::env::var("ENABLE_TORZNAB_API")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(true),
            easynews_username: std::env::var("EASYNEWS_USERNAME").ok().filter(|s| !s.is_empty()),
            easynews_password: std::env::var("EASYNEWS_PASSWORD").ok().filter(|s| !s.is_empty()),
            torbox_search_ttl: std::env::var("TORBOX_SEARCH_TTL")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(1800),
            telegram_bot_token: std::env::var("TELEGRAM_BOT_TOKEN").ok().filter(|s| !s.is_empty()),
            telegram_bot_username: std::env::var("TELEGRAM_BOT_USERNAME").ok().filter(|s| !s.is_empty()),
            telegram_webhook_secret_token: std::env::var("TELEGRAM_WEBHOOK_SECRET_TOKEN").ok().filter(|s| !s.is_empty()),
            smtp_host: std::env::var("SMTP_HOST").ok().filter(|s| !s.is_empty()),
            smtp_port: std::env::var("SMTP_PORT").ok().and_then(|v| v.parse().ok()).unwrap_or(587),
            smtp_username: std::env::var("SMTP_USERNAME").ok().filter(|s| !s.is_empty()),
            smtp_password: std::env::var("SMTP_PASSWORD").ok().filter(|s| !s.is_empty()),
            smtp_from: std::env::var("SMTP_FROM_EMAIL")
                .unwrap_or_else(|_| "noreply@mediafusion.example.com".into()),
            sync_debrid_cache_streams: std::env::var("SYNC_DEBRID_CACHE_STREAMS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(false),
            telegram_api_id: std::env::var("TELEGRAM_API_ID")
                .ok()
                .and_then(|s| s.parse().ok()),
            telegram_api_hash: std::env::var("TELEGRAM_API_HASH")
                .ok()
                .filter(|s| !s.is_empty()),
            telegram_grammers_session: std::env::var("TELEGRAM_GRAMMERS_SESSION")
                .ok()
                .filter(|s| !s.is_empty()),
            telegram_scraping_channels: std::env::var("TELEGRAM_SCRAPING_CHANNELS")
                .ok()
                .map(|s| {
                    s.split(',')
                        .map(|c| c.trim().to_string())
                        .filter(|c| !c.is_empty())
                        .collect()
                })
                .unwrap_or_default(),
            telegram_scrape_message_limit: std::env::var("TELEGRAM_SCRAPE_MESSAGE_LIMIT")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(100),
            min_scraping_video_size: std::env::var("MIN_SCRAPING_VIDEO_SIZE")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(50 * 1024 * 1024),
            enable_iptv_import: std::env::var("ENABLE_IPTV_IMPORT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(true),
            allow_public_iptv_sharing: std::env::var("ALLOW_PUBLIC_IPTV_SHARING")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(false),
        }
    }
}
