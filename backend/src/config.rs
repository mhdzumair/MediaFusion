/// Application configuration loaded from environment variables.
#[derive(Clone)]
pub struct AppConfig {
    /// 32-byte padded key for AES-256-CBC stream decryption.
    pub secret_key: [u8; 32],
    /// Raw secret key string (used for HMAC-SHA256 manifest cache keys).
    pub secret_key_raw: String,
    pub postgres_uri: String,
    pub postgres_ro_uri: Option<String>,
    /// Redis URL — shared with the Python background workers. Reads REDIS_URL.
    pub redis_url: String,
    pub port: u16,
    pub meta_cache_ttl: u64,
    pub catalog_cache_ttl: u64,
    /// Absolute base URL of this service (e.g. "https://mediafusion.example.com").
    /// Used to build poster URLs in meta responses.
    pub host_url: String,
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
    pub zilean_url: String,
    pub jackett_url: Option<String>,
    pub jackett_api_key: Option<String>,
    pub mediafusion_url: String,
    /// Optional secret_str to use when calling the peer MediaFusion instance.
    /// Enables authenticated scraping (e.g. elfhosted which requires debrid creds).
    /// Format: "U-{uuid}" or "D-{encrypted}"
    pub mediafusion_secret_str: Option<String>,
    /// Mirrors Python's prowlarr_live_title_search (default: true).
    /// When false, prowlarr is excluded from live title searches.
    pub prowlarr_live_title_search: bool,

    // ── Instance mode ────────────────────────────────────────────────────────
    /// When true the instance is fully public: no api_password or X-API-Key
    /// checks are enforced anywhere. Mirrors Python's IS_PUBLIC_INSTANCE.
    /// Default: false (private).
    pub is_public_instance: bool,

    // ── Torznab / auth ───────────────────────────────────────────────────────
    /// Optional API password for Torznab and private-instance validation.
    pub api_password: Option<String>,
    /// Enable the Torznab feed endpoint (default: true).
    pub enable_torznab_api: bool,

    // ── Scraper enable flags (mirror Python settings.is_scrap_from_*) ───────
    pub is_scrap_from_prowlarr: bool,
    pub is_scrap_from_zilean: bool,
    pub is_scrap_from_torrentio: bool,
    pub is_scrap_from_mediafusion: bool,
    pub is_scrap_from_dmm_hashlist: bool,
    pub disable_dmm_hashlist_scraper: bool,
    pub is_scrap_from_public_indexers: bool,
    pub is_scrap_from_public_usenet_indexers: bool,
    pub is_scrap_from_jackett: bool,
    pub is_scrap_from_torznab: bool,

    // ── Scraper search TTLs in seconds (derived from interval env vars) ──────
    pub prowlarr_search_ttl: i64,
    pub zilean_search_ttl: i64,
    pub torrentio_search_ttl: i64,
    pub mediafusion_search_ttl: i64,
    pub dmm_hashlist_sync_ttl: i64,
    pub public_indexers_search_ttl: i64,
    pub public_usenet_search_ttl: i64,
    pub jackett_search_ttl: i64,
    pub torbox_search_ttl: i64,

    // ── Scraper query timeouts (seconds) ─────────────────────────────────────
    /// Per-request HTTP timeout for Prowlarr search calls.
    pub prowlarr_search_query_timeout: u64,
    /// Per-request HTTP timeout for Jackett search calls.
    pub jackett_search_query_timeout: u64,

    // ── Prowlarr immediate processing limits ─────────────────────────────────
    /// Max results to keep per indexer from a Prowlarr search.
    pub prowlarr_immediate_max_process: usize,
    /// Overall time budget (seconds) for the Prowlarr live scrape.
    pub prowlarr_immediate_max_process_time: u64,

    // ── Jackett immediate processing limits ──────────────────────────────────
    /// Max results to keep from a Jackett search.
    pub jackett_immediate_max_process: usize,
    /// Overall time budget (seconds) for the Jackett live scrape.
    pub jackett_immediate_max_process_time: u64,

    // ── Stream cache TTL ──────────────────────────────────────────────────────
    /// Redis TTL (seconds) for raw stream blobs. Default: 900.
    pub stream_raw_redis_cache_ttl: u64,

    // ── Telegram bot ────────────────────────────────────────────────────────
    pub telegram_bot_token: Option<String>,
    pub telegram_bot_username: Option<String>,
    pub telegram_webhook_secret_token: Option<String>,
    /// Chat ID for moderation notifications (TELEGRAM_CHAT_ID env var).
    pub telegram_chat_id: Option<String>,

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

    // ── Trakt / Simkl OAuth ─────────────────────────────────────────────────
    pub trakt_client_id: Option<String>,
    pub trakt_client_secret: Option<String>,
    pub simkl_client_id: Option<String>,
    pub simkl_client_secret: Option<String>,

    // ── Scheduler (global) ───────────────────────────────────────────────────
    /// When true all scheduler jobs are suppressed regardless of individual flags.
    pub disable_all_scheduler: bool,

    // ── Scheduler: Scrapy spiders ────────────────────────────────────────────
    pub tamilmv_scheduler_crontab: String,
    pub disable_tamilmv_scheduler: bool,
    pub tamil_blasters_scheduler_crontab: String,
    pub disable_tamil_blasters_scheduler: bool,
    pub formula_ext_scheduler_crontab: String,
    pub disable_formula_ext_scheduler: bool,
    pub motogp_ext_scheduler_crontab: String,
    pub disable_motogp_ext_scheduler: bool,
    pub wwe_ext_scheduler_crontab: String,
    pub disable_wwe_ext_scheduler: bool,
    pub ufc_ext_scheduler_crontab: String,
    pub disable_ufc_ext_scheduler: bool,
    pub movies_tv_ext_scheduler_crontab: String,
    pub disable_movies_tv_ext_scheduler: bool,
    pub nowmetv_scheduler_crontab: String,
    pub disable_nowmetv_scheduler: bool,
    pub nowsports_scheduler_crontab: String,
    pub disable_nowsports_scheduler: bool,
    pub tamilultra_scheduler_crontab: String,
    pub disable_tamilultra_scheduler: bool,
    pub sport_video_scheduler_crontab: String,
    pub disable_sport_video_scheduler: bool,
    pub dlhd_scheduler_crontab: String,
    pub disable_dlhd_scheduler: bool,
    pub arab_torrents_scheduler_crontab: String,
    pub disable_arab_torrents_scheduler: bool,
    pub x1337_scheduler_crontab: String,
    pub disable_x1337_scheduler: bool,
    pub thepiratebay_scheduler_crontab: String,
    pub disable_thepiratebay_scheduler: bool,
    pub rutor_scheduler_crontab: String,
    pub disable_rutor_scheduler: bool,
    pub limetorrents_scheduler_crontab: String,
    pub disable_limetorrents_scheduler: bool,
    pub yts_scheduler_crontab: String,
    pub disable_yts_scheduler: bool,
    pub bt4g_scheduler_crontab: String,
    pub disable_bt4g_scheduler: bool,
    pub nyaa_scheduler_crontab: String,
    pub disable_nyaa_scheduler: bool,
    pub animetosho_scheduler_crontab: String,
    pub disable_animetosho_scheduler: bool,
    pub subsplease_scheduler_crontab: String,
    pub disable_subsplease_scheduler: bool,
    pub animepahe_scheduler_crontab: String,
    pub disable_animepahe_scheduler: bool,
    pub bt52_scheduler_crontab: String,
    pub disable_bt52_scheduler: bool,
    pub uindex_scheduler_crontab: String,
    pub disable_uindex_scheduler: bool,
    pub eztv_rss_scheduler_crontab: String,
    pub disable_eztv_rss_scheduler: bool,

    // ── Scheduler: Feed scrapers ─────────────────────────────────────────────
    pub prowlarr_feed_scraper_crontab: String,
    pub disable_prowlarr_feed_scraper: bool,
    pub jackett_feed_scraper_crontab: String,
    pub disable_jackett_feed_scraper: bool,
    pub rss_feed_scraper_crontab: String,
    pub disable_rss_feed_scraper: bool,

    // ── Scheduler: Background scrapers ───────────────────────────────────────
    pub dmm_hashlist_scraper_crontab: String,
    pub youtube_background_scraper_crontab: String,
    pub disable_youtube_background_scraper: bool,
    pub acestream_background_scraper_crontab: String,
    pub disable_acestream_background_scraper: bool,
    pub telegram_background_scraper_crontab: String,
    pub disable_telegram_background_scraper: bool,

    // ── Scheduler: Maintenance tasks ─────────────────────────────────────────
    pub validate_tv_streams_in_db_crontab: String,
    pub disable_validate_tv_streams_in_db: bool,
    pub update_seeders_crontab: String,
    pub disable_update_seeders: bool,
    pub cleanup_expired_scraper_task_crontab: String,
    pub cleanup_expired_cache_task_crontab: String,
    pub background_search_crontab: String,
    pub integration_sync_crontab: String,
    pub disable_integration_sync_scheduler: bool,

    // ── Discover / TMDB ───────────────────────────────────────────
    /// TMDB API key (server-level fallback for discover endpoints).
    pub tmdb_api_key: Option<String>,
    /// Enable the Discover feature endpoints (default: true).
    pub discover_enabled: bool,
    /// Allow server-level TMDB key to be used as fallback when user has none (default: false).
    pub discover_allow_server_key: bool,

    // ── Image upload ──────────────────────────────────────────────
    /// Enable local image upload endpoint (default: false).
    pub image_upload_enabled: bool,
    /// Directory to store uploaded images (default: "./data/images").
    pub images_dir: String,

    // ── Exception tracking ────────────────────────────────────────
    pub enable_exception_tracking: bool,
    /// TTL in seconds for exception records in Redis (default: 259200 = 3 days).
    pub exception_tracking_ttl: i64,
    /// Maximum number of distinct exceptions to keep in Redis (default: 500).
    pub exception_tracking_max_entries: i64,

    // ── Static resources ──────────────────────────────────────────
    /// Root of the resources/ tree served at /static (mirrors Python's StaticFiles mount).
    /// Defaults to resources/ (relative to working directory, correct for Docker WORKDIR=/mediafusion).
    pub resources_dir: String,
    /// Path to the built React SPA dist/ directory served at /app.
    /// Defaults to clients/frontend/dist (matches Docker COPY destination).
    pub frontend_dist_dir: String,

    // ── Request timeouts ──────────────────────────────────────────
    /// Timeout for /stream/ routes in seconds. Live search scrapes run inline so
    /// this needs to be longer than the slowest scraper. Default: 120.
    pub request_timeout: u64,

    // ── Browser automation (Browserless v2 + Byparr) ─────────────
    /// Browserless v2 base URL (e.g. `http://browserless:3000`).
    /// Used by spiders that need real Chrome execution to bypass JS bot challenges
    /// (e.g. adm.tools on sport-video.org.ua).
    pub browserless_url: Option<String>,
    /// Byparr (FlareSolverr-compatible) base URL. When set, Cloudflare-protected
    /// public indexers (1337x, TPB, etc.) are fetched via Byparr instead of plain HTTP.
    pub byparr_url: Option<String>,
    /// Comma-separated list of public indexer keys to enable (e.g. "x1337,nyaa").
    /// When unset, all indexers matching the media type are used.
    pub public_indexers_live_search_sites: Option<String>,

    // ── Public indexer source health gates ───────────────────────────────────
    pub public_indexers_source_health_gates_enabled: bool,
    pub public_indexers_source_health_min_samples: i64,
    pub public_indexers_source_min_success_rate: f64,
    pub public_indexers_source_max_timeout_rate: f64,
    pub public_indexers_source_health_counter_soft_cap: i64,
    pub public_indexers_source_health_decay_factor: f64,
    pub public_indexers_source_health_recovery_success_streak: i64,
    pub public_indexers_source_health_scope_mode: String,
    pub public_indexers_source_health_scope: String,
    pub public_indexers_source_health_metrics_ttl_seconds: i64,

    // ── Provider restrictions ─────────────────────────────────────
    /// Mirrors Python's `disabled_providers`. JSON array of provider service names to
    /// block globally, e.g. `'["p2p","realdebrid"]'`. "p2p" disables WebTorrent fallback.
    pub disabled_providers: Vec<String>,
    /// Mirrors Python's `disabled_content_types`. JSON array of content type strings to
    /// block globally, e.g. `'["magnet","torrent"]'`.
    pub disabled_content_types: Vec<String>,
    /// Max streaming providers a single profile may have. Default: 5.
    pub max_streaming_providers_per_profile: u32,
    /// Provider signup link map. JSON object keyed by service name.
    pub provider_signup_links: std::collections::HashMap<String, Vec<String>>,
    /// Whether NZB file import is enabled. Default: true.
    pub enable_nzb_file_import: bool,
    /// Operator-configured NzbDAV URL (auto-injected into profiles when set).
    pub default_nzbdav_url: Option<String>,
    pub default_nzbdav_api_key: Option<String>,
    /// Premiumize OAuth client credentials (enables OAuth flow in UI).
    pub premiumize_oauth_client_id: Option<String>,
    pub premiumize_oauth_client_secret: Option<String>,
    /// Branding description shown on the home page (may contain HTML).
    pub branding_description: String,
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

        // Try UPPER_CASE first, fall back to lower_case — mirrors Python pydantic-settings
        // case-insensitive behaviour.
        fn env(key: &str) -> Result<String, std::env::VarError> {
            std::env::var(key).or_else(|_| std::env::var(key.to_lowercase()))
        }

        let raw = env("SECRET_KEY").expect("SECRET_KEY required");
        let mut key = [b' '; 32];
        let b = raw.as_bytes();
        key[..b.len().min(32)].copy_from_slice(&b[..b.len().min(32)]);

        let contact_email = env("CONTACT_EMAIL")
            .ok()
            .filter(|e| !e.is_empty() && e != "admin@example.com");

        AppConfig {
            secret_key: key,
            secret_key_raw: raw,
            postgres_uri: env("POSTGRES_URI")
                .unwrap_or_else(|_| {
                    "postgresql://mediafusion:mediafusion@127.0.0.1:5432/mediafusion".into()
                })
                .replace("postgresql+asyncpg://", "postgresql://"),
            postgres_ro_uri: env("POSTGRES_READ_URI")
                .ok()
                .filter(|s| !s.is_empty())
                .map(|s| s.replace("postgresql+asyncpg://", "postgresql://")),
            redis_url: env("REDIS_URL")
                .unwrap_or_else(|_| "redis://127.0.0.1:6379".into()),
            port: env("STREAM_RS_PORT")
                .unwrap_or_else(|_| "8000".into())
                .parse()
                .unwrap_or(8000),
            meta_cache_ttl: env("META_CACHE_TTL_SECONDS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(1800),
            catalog_cache_ttl: env("CATALOG_CACHE_TTL_SECONDS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(1800),
            host_url: env("HOST_URL")
                .unwrap_or_else(|_| "http://localhost:8000".into())
                .trim_end_matches('/')
                .to_string(),
            addon_name: env("ADDON_NAME")
                .unwrap_or_else(|_| "MediaFusion".into()),
            addon_version: env("VERSION")
                .unwrap_or_else(|_| "1.0.0".into()),
            addon_description: env("ADDON_DESCRIPTION").unwrap_or_else(|_| {
                "MediaFusion — universal torrent & debrid streaming addon for Stremio".into()
            }),
            logo_url: env("ADDON_LOGO").unwrap_or_else(|_| {
                "https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/images/mediafusion_logo.png".into()
            }),
            contact_email,
            prowlarr_url: env("PROWLARR_URL").ok()
                .filter(|s| !s.is_empty())
                .map(|u| u.trim_end_matches('/').to_string()),
            prowlarr_api_key: env("PROWLARR_API_KEY").ok().filter(|s| !s.is_empty()),
            torrentio_url: env("TORRENTIO_URL")
                .unwrap_or_else(|_| "https://torrentio.strem.fun".into())
                .trim_end_matches('/')
                .to_string(),
            zilean_url: env("ZILEAN_URL")
                .unwrap_or_else(|_| "https://zilean.elfhosted.com".into())
                .trim_end_matches('/')
                .to_string(),
            jackett_url: env("JACKETT_URL").ok()
                .filter(|s| !s.is_empty())
                .map(|u| u.trim_end_matches('/').to_string()),
            jackett_api_key: env("JACKETT_API_KEY").ok().filter(|s| !s.is_empty()),
            mediafusion_url: env("MEDIAFUSION_URL")
                .unwrap_or_else(|_| "https://mediafusion.elfhosted.com".into())
                .trim_end_matches('/')
                .to_string(),
            mediafusion_secret_str: env("MEDIAFUSION_SECRET_STR").ok().filter(|s| !s.is_empty()),
            prowlarr_live_title_search: env("PROWLARR_LIVE_TITLE_SEARCH")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            is_public_instance: env("IS_PUBLIC_INSTANCE")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            api_password: env("API_PASSWORD").ok().filter(|s| !s.is_empty()),
            enable_torznab_api: env("ENABLE_TORZNAB_API")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            is_scrap_from_prowlarr: env("IS_SCRAP_FROM_PROWLARR")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            is_scrap_from_zilean: env("IS_SCRAP_FROM_ZILEAN")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            is_scrap_from_torrentio: env("IS_SCRAP_FROM_TORRENTIO")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            is_scrap_from_mediafusion: env("IS_SCRAP_FROM_MEDIAFUSION")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            is_scrap_from_dmm_hashlist: env("IS_SCRAP_FROM_DMM_HASHLIST")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            disable_dmm_hashlist_scraper: env("DISABLE_DMM_HASHLIST_SCRAPER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            is_scrap_from_public_indexers: env("IS_SCRAP_FROM_PUBLIC_INDEXERS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            is_scrap_from_public_usenet_indexers: env("IS_SCRAP_FROM_PUBLIC_USENET_INDEXERS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            is_scrap_from_jackett: env("IS_SCRAP_FROM_JACKETT")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            is_scrap_from_torznab: env("IS_SCRAP_FROM_TORZNAB")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            prowlarr_search_ttl: env("PROWLARR_SEARCH_INTERVAL_HOUR")
                .ok().and_then(|v| v.parse::<i64>().ok()).unwrap_or(72) * 3600,
            zilean_search_ttl: env("ZILEAN_SEARCH_INTERVAL_HOUR")
                .ok().and_then(|v| v.parse::<i64>().ok()).unwrap_or(24) * 3600,
            torrentio_search_ttl: env("TORRENTIO_SEARCH_INTERVAL_DAYS")
                .ok().and_then(|v| v.parse::<i64>().ok()).unwrap_or(3) * 86400,
            mediafusion_search_ttl: env("MEDIAFUSION_SEARCH_INTERVAL_DAYS")
                .ok().and_then(|v| v.parse::<i64>().ok()).unwrap_or(3) * 86400,
            dmm_hashlist_sync_ttl: env("DMM_HASHLIST_SYNC_INTERVAL_HOUR")
                .ok().and_then(|v| v.parse::<i64>().ok()).unwrap_or(6) * 3600,
            public_indexers_search_ttl: env("PUBLIC_INDEXERS_SEARCH_INTERVAL_HOUR")
                .ok().and_then(|v| v.parse::<i64>().ok()).unwrap_or(48) * 3600,
            public_usenet_search_ttl: env("PUBLIC_USENET_INDEXERS_SEARCH_INTERVAL_HOUR")
                .ok().and_then(|v| v.parse::<i64>().ok()).unwrap_or(48) * 3600,
            jackett_search_ttl: env("JACKETT_SEARCH_INTERVAL_HOUR")
                .ok().and_then(|v| v.parse::<i64>().ok()).unwrap_or(72) * 3600,
            torbox_search_ttl: {
                let prowlarr_h = env("PROWLARR_SEARCH_INTERVAL_HOUR")
                    .ok().and_then(|v| v.parse::<i64>().ok()).unwrap_or(72);
                env("TORBOX_SEARCH_TTL")
                    .ok().and_then(|v| v.parse::<i64>().ok())
                    .unwrap_or(prowlarr_h * 3600)
            },
            telegram_bot_token: env("TELEGRAM_BOT_TOKEN").ok().filter(|s| !s.is_empty()),
            telegram_bot_username: env("TELEGRAM_BOT_USERNAME").ok().filter(|s| !s.is_empty()),
            telegram_webhook_secret_token: env("TELEGRAM_WEBHOOK_SECRET_TOKEN").ok().filter(|s| !s.is_empty()),
            telegram_chat_id: env("TELEGRAM_CHAT_ID").ok().filter(|s| !s.is_empty()),
            smtp_host: env("SMTP_HOST").ok().filter(|s| !s.is_empty()),
            smtp_port: env("SMTP_PORT").ok().and_then(|v| v.parse().ok()).unwrap_or(587),
            smtp_username: env("SMTP_USERNAME").ok().filter(|s| !s.is_empty()),
            smtp_password: env("SMTP_PASSWORD").ok().filter(|s| !s.is_empty()),
            smtp_from: env("SMTP_FROM_EMAIL")
                .unwrap_or_else(|_| "noreply@mediafusion.example.com".into()),
            sync_debrid_cache_streams: env("SYNC_DEBRID_CACHE_STREAMS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            telegram_api_id: env("TELEGRAM_API_ID")
                .ok().and_then(|s| s.parse().ok()),
            telegram_api_hash: env("TELEGRAM_API_HASH").ok().filter(|s| !s.is_empty()),
            telegram_grammers_session: env("TELEGRAM_GRAMMERS_SESSION").ok().filter(|s| !s.is_empty()),
            telegram_scraping_channels: env("TELEGRAM_SCRAPING_CHANNELS")
                .ok()
                .map(|s| {
                    s.split(',')
                        .map(|c| c.trim().to_string())
                        .filter(|c| !c.is_empty())
                        .collect()
                })
                .unwrap_or_default(),
            telegram_scrape_message_limit: env("TELEGRAM_SCRAPE_MESSAGE_LIMIT")
                .ok().and_then(|s| s.parse().ok()).unwrap_or(100),
            min_scraping_video_size: env("MIN_SCRAPING_VIDEO_SIZE")
                .ok().and_then(|s| s.parse().ok()).unwrap_or(26_214_400),
            enable_iptv_import: env("ENABLE_IPTV_IMPORT")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            allow_public_iptv_sharing: env("ALLOW_PUBLIC_IPTV_SHARING")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            trakt_client_id: env("TRAKT_CLIENT_ID").ok().filter(|s| !s.is_empty()),
            trakt_client_secret: env("TRAKT_CLIENT_SECRET").ok().filter(|s| !s.is_empty()),
            simkl_client_id: env("SIMKL_CLIENT_ID").ok().filter(|s| !s.is_empty()),
            simkl_client_secret: env("SIMKL_CLIENT_SECRET").ok().filter(|s| !s.is_empty()),
            disable_all_scheduler: env("DISABLE_ALL_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            tamilmv_scheduler_crontab: env("TAMILMV_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 */3 * * *".into()),
            disable_tamilmv_scheduler: env("DISABLE_TAMILMV_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            tamil_blasters_scheduler_crontab: env("TAMIL_BLASTERS_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 */6 * * *".into()),
            disable_tamil_blasters_scheduler: env("DISABLE_TAMIL_BLASTERS_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            formula_ext_scheduler_crontab: env("FORMULA_EXT_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "*/30 * * * *".into()),
            disable_formula_ext_scheduler: env("DISABLE_FORMULA_EXT_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            motogp_ext_scheduler_crontab: env("MOTOGP_EXT_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 5 * * *".into()),
            disable_motogp_ext_scheduler: env("DISABLE_MOTOGP_EXT_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            wwe_ext_scheduler_crontab: env("WWE_EXT_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "10 */3 * * *".into()),
            disable_wwe_ext_scheduler: env("DISABLE_WWE_EXT_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            ufc_ext_scheduler_crontab: env("UFC_EXT_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "30 */3 * * *".into()),
            disable_ufc_ext_scheduler: env("DISABLE_UFC_EXT_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            movies_tv_ext_scheduler_crontab: env("MOVIES_TV_EXT_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 * * * *".into()),
            disable_movies_tv_ext_scheduler: env("DISABLE_MOVIES_TV_EXT_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            nowmetv_scheduler_crontab: env("NOWMETV_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 0 * * 5".into()),
            disable_nowmetv_scheduler: env("DISABLE_NOWMETV_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            nowsports_scheduler_crontab: env("NOWSPORTS_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 10 * * 5".into()),
            disable_nowsports_scheduler: env("DISABLE_NOWSPORTS_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            tamilultra_scheduler_crontab: env("TAMILULTRA_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 8 * * 5".into()),
            disable_tamilultra_scheduler: env("DISABLE_TAMILULTRA_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            sport_video_scheduler_crontab: env("SPORT_VIDEO_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "*/20 * * * *".into()),
            disable_sport_video_scheduler: env("DISABLE_SPORT_VIDEO_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            dlhd_scheduler_crontab: env("DLHD_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 0 * * 1".into()),
            disable_dlhd_scheduler: env("DISABLE_DLHD_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            arab_torrents_scheduler_crontab: env("ARAB_TORRENTS_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 0 * * *".into()),
            disable_arab_torrents_scheduler: env("DISABLE_ARAB_TORRENTS_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            x1337_scheduler_crontab: env("X1337_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 */6 * * *".into()),
            disable_x1337_scheduler: env("DISABLE_X1337_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            thepiratebay_scheduler_crontab: env("THEPIRATEBAY_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "30 */6 * * *".into()),
            disable_thepiratebay_scheduler: env("DISABLE_THEPIRATEBAY_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            rutor_scheduler_crontab: env("RUTOR_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "45 */6 * * *".into()),
            disable_rutor_scheduler: env("DISABLE_RUTOR_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            limetorrents_scheduler_crontab: env("LIMETORRENTS_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 */8 * * *".into()),
            disable_limetorrents_scheduler: env("DISABLE_LIMETORRENTS_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            yts_scheduler_crontab: env("YTS_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 */12 * * *".into()),
            disable_yts_scheduler: env("DISABLE_YTS_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            bt4g_scheduler_crontab: env("BT4G_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "15 */8 * * *".into()),
            disable_bt4g_scheduler: env("DISABLE_BT4G_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            nyaa_scheduler_crontab: env("NYAA_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "15 */3 * * *".into()),
            disable_nyaa_scheduler: env("DISABLE_NYAA_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            animetosho_scheduler_crontab: env("ANIMETOSHO_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "30 */4 * * *".into()),
            disable_animetosho_scheduler: env("DISABLE_ANIMETOSHO_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            subsplease_scheduler_crontab: env("SUBSPLEASE_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "45 */4 * * *".into()),
            disable_subsplease_scheduler: env("DISABLE_SUBSPLEASE_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            animepahe_scheduler_crontab: env("ANIMEPAHE_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 */6 * * *".into()),
            disable_animepahe_scheduler: env("DISABLE_ANIMEPAHE_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            bt52_scheduler_crontab: env("BT52_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "30 */6 * * *".into()),
            disable_bt52_scheduler: env("DISABLE_BT52_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            uindex_scheduler_crontab: env("UINDEX_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 */4 * * *".into()),
            disable_uindex_scheduler: env("DISABLE_UINDEX_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            eztv_rss_scheduler_crontab: env("EZTV_RSS_SCHEDULER_CRONTAB")
                .unwrap_or_else(|_| "0 */2 * * *".into()),
            disable_eztv_rss_scheduler: env("DISABLE_EZTV_RSS_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            prowlarr_feed_scraper_crontab: env("PROWLARR_FEED_SCRAPER_CRONTAB")
                .unwrap_or_else(|_| "0 */3 * * *".into()),
            disable_prowlarr_feed_scraper: env("DISABLE_PROWLARR_FEED_SCRAPER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            jackett_feed_scraper_crontab: env("JACKETT_FEED_SCRAPER_CRONTAB")
                .unwrap_or_else(|_| "0 */3 * * *".into()),
            disable_jackett_feed_scraper: env("DISABLE_JACKETT_FEED_SCRAPER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            rss_feed_scraper_crontab: env("RSS_FEED_SCRAPER_CRONTAB")
                .unwrap_or_else(|_| "0 */3 * * *".into()),
            disable_rss_feed_scraper: env("DISABLE_RSS_FEED_SCRAPER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            dmm_hashlist_scraper_crontab: env("DMM_HASHLIST_SCRAPER_CRONTAB")
                .unwrap_or_else(|_| "0 * * * *".into()),
            youtube_background_scraper_crontab: env("YOUTUBE_BACKGROUND_SCRAPER_CRONTAB")
                .unwrap_or_else(|_| "20 */6 * * *".into()),
            disable_youtube_background_scraper: env("DISABLE_YOUTUBE_BACKGROUND_SCRAPER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            acestream_background_scraper_crontab: env("ACESTREAM_BACKGROUND_SCRAPER_CRONTAB")
                .unwrap_or_else(|_| "40 */6 * * *".into()),
            disable_acestream_background_scraper: env("DISABLE_ACESTREAM_BACKGROUND_SCRAPER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            telegram_background_scraper_crontab: env("TELEGRAM_BACKGROUND_SCRAPER_CRONTAB")
                .unwrap_or_else(|_| "10 */6 * * *".into()),
            disable_telegram_background_scraper: env("DISABLE_TELEGRAM_BACKGROUND_SCRAPER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            validate_tv_streams_in_db_crontab: env("VALIDATE_TV_STREAMS_IN_DB_CRONTAB")
                .unwrap_or_else(|_| "0 0 * * 4".into()),
            disable_validate_tv_streams_in_db: env("DISABLE_VALIDATE_TV_STREAMS_IN_DB")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            update_seeders_crontab: env("UPDATE_SEEDERS_CRONTAB")
                .unwrap_or_else(|_| "0 0 * * 3".into()),
            disable_update_seeders: env("DISABLE_UPDATE_SEEDERS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            cleanup_expired_scraper_task_crontab: env("CLEANUP_EXPIRED_SCRAPER_TASK_CRONTAB")
                .unwrap_or_else(|_| "0 * * * *".into()),
            cleanup_expired_cache_task_crontab: env("CLEANUP_EXPIRED_CACHE_TASK_CRONTAB")
                .unwrap_or_else(|_| "0 0 * * *".into()),
            background_search_crontab: env("BACKGROUND_SEARCH_CRONTAB")
                .unwrap_or_else(|_| "*/3 * * * *".into()),
            integration_sync_crontab: env("INTEGRATION_SYNC_CRONTAB")
                .unwrap_or_else(|_| "0 */6 * * *".into()),
            disable_integration_sync_scheduler: env("DISABLE_INTEGRATION_SYNC_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            tmdb_api_key: env("TMDB_API_KEY").ok().filter(|s| !s.is_empty()),
            discover_enabled: env("DISCOVER_ENABLED")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            discover_allow_server_key: env("DISCOVER_ALLOW_SERVER_KEY")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            image_upload_enabled: env("IMAGE_UPLOAD_ENABLED")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            images_dir: env("IMAGES_DIR").unwrap_or_else(|_| "./data/images".into()),
            enable_exception_tracking: env("ENABLE_EXCEPTION_TRACKING")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            exception_tracking_ttl: env("EXCEPTION_TRACKING_TTL")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(259200),
            exception_tracking_max_entries: env("EXCEPTION_TRACKING_MAX_ENTRIES")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(500),
            resources_dir: env("RESOURCES_DIR").unwrap_or_else(|_| "resources".into()),
            frontend_dist_dir: env("FRONTEND_DIST_DIR")
                .unwrap_or_else(|_| "clients/frontend/dist".into()),
            request_timeout: env("REQUEST_TIMEOUT")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(120),
            prowlarr_search_query_timeout: env("PROWLARR_SEARCH_QUERY_TIMEOUT")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(30),
            jackett_search_query_timeout: env("JACKETT_SEARCH_QUERY_TIMEOUT")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(30),
            prowlarr_immediate_max_process: env("PROWLARR_IMMEDIATE_MAX_PROCESS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(10),
            prowlarr_immediate_max_process_time: env("PROWLARR_IMMEDIATE_MAX_PROCESS_TIME")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(15),
            jackett_immediate_max_process: env("JACKETT_IMMEDIATE_MAX_PROCESS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(10),
            jackett_immediate_max_process_time: env("JACKETT_IMMEDIATE_MAX_PROCESS_TIME")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(15),
            stream_raw_redis_cache_ttl: env("STREAM_RAW_REDIS_CACHE_TTL_SECONDS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(900),
            browserless_url: env("BROWSERLESS_URL").ok()
                .filter(|s| !s.is_empty())
                .map(|u| u.trim_end_matches('/').to_string()),
            byparr_url: env("BYPARR_URL").ok()
                .filter(|s| !s.is_empty())
                .map(|u| u.trim_end_matches('/').to_string()),
            public_indexers_live_search_sites: env("PUBLIC_INDEXERS_LIVE_SEARCH_SITES")
                .ok().filter(|s| !s.is_empty()),
            public_indexers_source_health_gates_enabled: env("PUBLIC_INDEXERS_SOURCE_HEALTH_GATES_ENABLED")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            public_indexers_source_health_min_samples: env("PUBLIC_INDEXERS_SOURCE_HEALTH_MIN_SAMPLES")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(10),
            public_indexers_source_min_success_rate: env("PUBLIC_INDEXERS_SOURCE_MIN_SUCCESS_RATE")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(0.35),
            public_indexers_source_max_timeout_rate: env("PUBLIC_INDEXERS_SOURCE_MAX_TIMEOUT_RATE")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(0.35),
            public_indexers_source_health_counter_soft_cap: env("PUBLIC_INDEXERS_SOURCE_HEALTH_COUNTER_SOFT_CAP")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(120),
            public_indexers_source_health_decay_factor: env("PUBLIC_INDEXERS_SOURCE_HEALTH_DECAY_FACTOR")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(0.5),
            public_indexers_source_health_recovery_success_streak: env("PUBLIC_INDEXERS_SOURCE_HEALTH_RECOVERY_SUCCESS_STREAK")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(2),
            public_indexers_source_health_scope_mode: env("PUBLIC_INDEXERS_SOURCE_HEALTH_SCOPE_MODE")
                .unwrap_or_else(|_| "pod".into()),
            public_indexers_source_health_scope: env("PUBLIC_INDEXERS_SOURCE_HEALTH_SCOPE")
                .unwrap_or_else(|_| String::new()),
            public_indexers_source_health_metrics_ttl_seconds: env("PUBLIC_INDEXERS_SOURCE_HEALTH_METRICS_TTL_SECONDS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(86400),
            disabled_providers: env("DISABLED_PROVIDERS")
                .ok().and_then(|s| serde_json::from_str::<Vec<String>>(&s).ok())
                .unwrap_or_default(),
            disabled_content_types: env("DISABLED_CONTENT_TYPES")
                .ok().and_then(|s| serde_json::from_str::<Vec<String>>(&s).ok())
                .unwrap_or_default(),
            max_streaming_providers_per_profile: env("MAX_STREAMING_PROVIDERS_PER_PROFILE")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(5),
            provider_signup_links: env("PROVIDER_SIGNUP_LINKS")
                .ok().and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or_default(),
            enable_nzb_file_import: env("ENABLE_NZB_FILE_IMPORT")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            default_nzbdav_url: env("DEFAULT_NZBDAV_URL").ok().filter(|s| !s.is_empty()),
            default_nzbdav_api_key: env("DEFAULT_NZBDAV_API_KEY").ok().filter(|s| !s.is_empty()),
            premiumize_oauth_client_id: env("PREMIUMIZE_OAUTH_CLIENT_ID").ok().filter(|s| !s.is_empty()),
            premiumize_oauth_client_secret: env("PREMIUMIZE_OAUTH_CLIENT_SECRET").ok().filter(|s| !s.is_empty()),
            branding_description: env("BRANDING_DESCRIPTION").unwrap_or_default(),
        }
    }
}
