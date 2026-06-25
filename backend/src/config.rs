/// Application configuration loaded from environment variables.
#[derive(Clone)]
pub struct AppConfig {
    /// 32-byte padded key for AES-256-CBC stream decryption.
    pub secret_key: [u8; 32],
    /// Raw secret key string (used for HMAC-SHA256 manifest cache keys).
    pub secret_key_raw: String,
    pub postgres_uri: String,
    pub postgres_ro_uri: Option<String>,
    /// Max connections for the read-write pool (`DB_POOL_SIZE`, default 10).
    /// Two pools exist (rw + ro), so the DB sees up to `db_pool_size + db_pool_size_ro` total.
    pub db_pool_size: u32,
    /// Max connections for the read-only pool (`DB_POOL_SIZE_RO`). Defaults to `db_pool_size`
    /// when unset so existing deployments are unchanged.
    pub db_pool_size_ro: Option<u32>,
    /// Minimum idle connections per pool (`DB_POOL_MIN`, default 2).
    pub db_pool_min: u32,
    /// How long a connection checkout waits before failing (`DB_ACQUIRE_TIMEOUT_SECS`, default 5).
    pub db_acquire_timeout_secs: u64,
    /// Drop idle connections older than this many seconds (`DB_IDLE_TIMEOUT_SECS`, default 600).
    pub db_idle_timeout_secs: u64,
    /// Recycle connections older than this many seconds — ensures endpoint re-resolution after
    /// a failover (`DB_MAX_LIFETIME_SECS`, default 1800).
    pub db_max_lifetime_secs: u64,
    /// Per-session `statement_timeout` in milliseconds (`DB_STATEMENT_TIMEOUT_MS`, default 60 000).
    pub db_statement_timeout_ms: u64,
    /// Per-session `idle_in_transaction_session_timeout` in milliseconds
    /// (`DB_IDLE_TX_TIMEOUT_MS`, default 60 000).
    pub db_idle_tx_timeout_ms: u64,
    /// Redis URL — shared with the Python background workers. Reads REDIS_URL.
    pub redis_url: String,
    pub port: u16,
    pub meta_cache_ttl: u64,
    pub catalog_cache_ttl: u64,
    /// Absolute base URL of this service (e.g. "https://mediafusion.example.com").
    /// Used to build poster URLs in meta responses.
    pub host_url: String,
    /// Base URL for generated poster images. Defaults to [`host_url`] when unset.
    pub poster_host_url: String,
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
    /// When false, title queries are omitted from live Prowlarr/Jackett searches.
    pub prowlarr_live_title_search: bool,
    /// Mirrors Python's jackett_live_title_search (default: true).
    pub jackett_live_title_search: bool,

    // ── Background search ─────────────────────────────────────────────────────
    /// When false, stream requests do not enqueue items for background re-scraping.
    pub background_search_enabled: bool,
    /// Max results to process per indexer during background search (default: 50).
    pub background_max_process: usize,
    /// Overall time budget (seconds) for background Prowlarr/Jackett scrapes.
    pub background_max_process_time: u64,
    /// Per-request HTTP timeout (seconds) for background Prowlarr/Jackett calls.
    pub background_query_timeout: u64,
    /// Minimum hours between background re-scrapes for the same queued item.
    pub background_search_interval_hours: i64,

    // ── Instance mode ────────────────────────────────────────────────────────
    /// When true the instance is fully public: no api_password or X-API-Key
    /// checks are enforced anywhere. Mirrors Python's IS_PUBLIC_INSTANCE.
    /// Default: false (private).
    pub is_public_instance: bool,

    // ── Torznab / auth ───────────────────────────────────────────────────────
    /// Optional API password for Torznab and private-instance validation.
    pub api_password: Option<String>,
    /// Expose the Prometheus /api/v1/metrics endpoint (default: false).
    pub enable_prometheus_metrics: bool,
    /// Bearer token required to scrape /api/v1/metrics.
    /// When set, the header `Authorization: Bearer <token>` is required on every
    /// scrape request — even on public instances.  Configure the matching
    /// `bearer_token` in your Prometheus scrape_configs.
    /// When unset and the endpoint is enabled, it is open to anyone who can
    /// reach the endpoint (rely on network-level controls in that case).
    pub metrics_api_key: Option<String>,
    /// When false, inbound HTTP rate limiting is disabled (Python `enable_rate_limit`).
    pub enable_rate_limit: bool,
    /// Enable the Torznab feed endpoint (default: true).
    pub enable_torznab_api: bool,

    // ── Scraper enable flags (mirror Python settings.is_scrap_from_*) ───────
    pub is_scrap_from_prowlarr: bool,
    pub is_scrap_from_zilean: bool,
    pub is_scrap_from_torrentio: bool,
    pub is_scrap_from_mediafusion: bool,
    pub is_scrap_from_dmm_hashlist: bool,
    pub disable_dmm_hashlist_scraper: bool,
    /// GitHub repo owner for DMM hashlist ingestion.
    pub dmm_hashlist_repo_owner: String,
    /// GitHub repo name for DMM hashlist ingestion.
    pub dmm_hashlist_repo_name: String,
    /// Git branch to read DMM hashlist commits from.
    pub dmm_hashlist_branch: String,
    /// Max new commits to process per incremental DMM hashlist run.
    pub dmm_hashlist_commits_per_run: usize,
    /// Max backfill commits to walk per DMM hashlist run.
    pub dmm_hashlist_backfill_commits_per_run: usize,
    /// Optional GitHub token for DMM hashlist fetches (`DMM_HASHLIST_GITHUB_TOKEN`, else `GITHUB_TOKEN`).
    /// Unauthenticated requests still work against the public GitHub API with lower rate limits.
    pub dmm_hashlist_github_token: Option<String>,
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
    /// Backup channel for storing contributed Telegram video files (TELEGRAM_BACKUP_CHANNEL_ID).
    pub telegram_backup_channel_id: Option<String>,

    // ── Auth ────────────────────────────────────────────────────────────────
    /// SMTP host for sending verification/reset emails (optional).
    pub smtp_host: Option<String>,
    pub smtp_port: u16,
    /// STARTTLS on port 587 (SMTP_USE_TLS, default true).
    pub smtp_use_tls: bool,
    /// Implicit SSL/TLS wrapper on port 465 (SMTP_USE_SSL, default false).
    pub smtp_use_ssl: bool,
    pub smtp_username: Option<String>,
    pub smtp_password: Option<String>,
    pub smtp_from: String,
    pub convertkit_api_key: Option<String>,
    pub convertkit_form_id: Option<String>,
    pub convertkit_newsletter_label: String,
    pub convertkit_newsletter_default_checked: bool,
    /// Optional partner/host SVG logo URL shown alongside the main logo.
    pub branding_svg: Option<String>,
    /// Default UI color scheme (e.g. "mediafusion", "cinematic", "ocean").
    pub default_color_scheme: String,

    // ── Debrid cache sync ───────────────────────────────────────────────────
    /// Whether to sync debrid cache to a central MediaFusion instance.
    pub sync_debrid_cache_streams: bool,
    /// Whether to store StremThru magnet cache entries in Redis. Default: false.
    pub store_stremthru_magnet_cache: bool,

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
    /// Server-level MDBList API key for list ingestion (`MDBLIST_API_KEY`).
    pub mdblist_api_key: Option<String>,
    pub simkl_client_id: Option<String>,
    pub simkl_client_secret: Option<String>,

    // ── Scheduler (global) ───────────────────────────────────────────────────
    /// When true all scheduler jobs are suppressed regardless of cron_jobs.enabled.
    pub disable_all_scheduler: bool,

    /// YouTube Data API key for import metadata (duration, geo-restriction).
    pub youtube_api_key: Option<String>,

    // ── Discover / TMDB ───────────────────────────────────────────
    /// TMDB API key (server-level fallback for discover endpoints).
    pub tmdb_api_key: Option<String>,
    /// TVDB API key for import metadata search (Python `settings.tvdb_api_key`).
    pub tvdb_api_key: Option<String>,
    /// When false, do not call v3-cinemeta.strem.io (mirrors Python `imdb_cinemeta_fallback_enabled`).
    pub imdb_cinemeta_fallback_enabled: bool,
    /// Base URL for IMDb non-commercial dataset files.
    pub imdb_datasets_base_url: String,
    /// Include adult titles when importing IMDb basics (default: false).
    pub imdb_import_include_adult: bool,
    /// Optional allowlist of dataset keys to import (empty = all).
    pub imdb_import_datasets: Vec<String>,
    /// Primary metadata source for scrapers (`imdb` or `tmdb`, Python `metadata_primary_source`).
    pub metadata_primary_source: String,
    /// Ordered anime provider chain for search/fetch (`kitsu`, `anilist`).
    pub anime_metadata_source_order: Vec<String>,
    /// Optional HTTP proxy for metadata/scraper requests (Python `requests_proxy_url`).
    pub requests_proxy_url: Option<String>,
    /// When true, live TV M3U8/MPD URLs are HEAD-checked before being returned (default: true).
    pub validate_m3u8_urls_liveness: bool,
    /// Enable the Discover feature endpoints (default: true).
    pub discover_enabled: bool,
    /// Allow server-level TMDB key to be used as fallback when user has none (default: false).
    pub discover_allow_server_key: bool,

    // ── Image upload ──────────────────────────────────────────────
    /// Enable local image upload endpoint (default: false).
    pub image_upload_enabled: bool,
    /// Directory to store uploaded images when S3 is not configured (default: "./data/images").
    pub images_dir: String,
    /// Image storage backend: `local` or `s3` (default: `s3` when S3 creds set, else `local`).
    pub image_storage_backend: String,

    // ── NZB file storage ──────────────────────────────────────────
    /// Where uploaded NZB files are stored: `local` or `s3` (default: `local`).
    pub nzb_file_storage_backend: String,
    /// Local directory for gzip-compressed NZB blobs (default: `./data/nzb`).
    pub nzb_dir: String,
    /// Signed NZB download URL lifetime in seconds (default: 3600).
    pub nzb_download_url_expiry: i64,

    // ── S3 / R2 object storage ────────────────────────────────────
    pub s3_endpoint_url: Option<String>,
    pub s3_access_key_id: Option<String>,
    pub s3_secret_access_key: Option<String>,
    pub s3_bucket_name: Option<String>,
    pub s3_region: String,

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
    pub scraper_config_path: String,
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

    // ── RealDebrid filename block patterns ────────────────────────
    /// Comma-separated substrings (case-insensitive) that cause RD to refuse a file.
    /// Default: webrip,bdrip,hdrip,dvdrip
    /// Set via env: RD_BLOCKED_SUBSTRINGS=webrip,bdrip,hdrip,dvdrip
    pub rd_blocked_substrings: Vec<String>,
    /// Comma-separated dot-adjacent source.codec pairs (case-insensitive) blocked by RD.
    /// Default: bluray.x264,hdtv.x264,hdtv.xvid,web.x264,web.h264
    /// Set via env: RD_BLOCKED_DOT_PAIRS=bluray.x264,hdtv.x264,hdtv.xvid,web.x264,web.h264
    pub rd_blocked_dot_pairs: Vec<String>,

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

    // ── NSFW poster classifier ────────────────────────────────────
    /// Enable NSFW poster classification filter in catalog queries (`POSTER_NSFW_ENABLED`, default true).
    pub poster_nsfw_enabled: bool,
    /// POSIX path to the ONNX model file (`POSTER_NSFW_MODEL_PATH`).
    /// Defaults to `resources/nsfw_model.onnx`. The classifier is disabled when the file is absent.
    pub poster_nsfw_model_path: String,
    /// Combined score threshold above which a poster is flagged (`POSTER_NSFW_THRESHOLD`, default 0.7).
    pub poster_nsfw_threshold: f32,
    /// Model version string — change this to force re-classification of all existing posters
    /// (`POSTER_NSFW_MODEL_VERSION`, default "v1").
    pub poster_nsfw_model_version: String,
    /// Batch size for the background NSFW scan job (`POSTER_NSFW_SCAN_BATCH`, default 100).
    pub poster_nsfw_scan_batch: usize,
    /// RPDB API key for fetching high-quality posters (`RPDB_API_KEY`).
    /// When set, the scan job fetches `https://api.ratingposterdb.com/{key}/imdb/poster-default/{imdb_id}.jpg?fallback=true`
    /// first and falls back to the stored `media_image` URL on failure.
    pub rpdb_api_key: Option<String>,

    // ── HTTP client / egress ──────────────────────────────────────
    /// Provider service IDs that bypass `REQUESTS_PROXY_URL` and connect directly.
    /// Comma-separated or JSON array. Valid IDs: realdebrid, seedr, debridlink, alldebrid,
    /// offcloud, pikpak, torbox, premiumize, stremthru, torrin, easydebrid, debrider.
    /// Ignored when `requests_proxy_include_debrid_providers` is non-empty.
    pub requests_proxy_exclude_debrid_providers: Vec<String>,
    /// When non-empty, ONLY these provider IDs are routed through `REQUESTS_PROXY_URL`;
    /// all others connect directly. Takes precedence over the exclude list.
    /// Same comma-separated or JSON array format and valid IDs as the exclude list.
    pub requests_proxy_include_debrid_providers: Vec<String>,
    /// When false, general (non-debrid-provider) HTTP calls bypass `REQUESTS_PROXY_URL`
    /// and connect directly. Debrid provider routing is unaffected. Default: true.
    pub requests_proxy_non_debrid_enabled: bool,
    /// TCP keepalive probe interval for all outbound HTTP clients (seconds). Default: 15.
    /// Keeps NAT/conntrack mappings alive through the gost tunnel during idle periods.
    pub tcp_keepalive_secs: u64,
    /// Enable the egress watchdog that exits the process when all probes fail for
    /// `egress_watchdog_fail_threshold` consecutive cycles (default: true).
    pub egress_watchdog_enabled: bool,
    /// Interval between watchdog probe cycles (seconds). Default: 30.
    pub egress_watchdog_interval_secs: u64,
    /// Consecutive failed cycles required before the watchdog triggers a restart.
    /// Default: 5 (~2.5 min of total egress loss at the default interval).
    pub egress_watchdog_fail_threshold: u32,
    /// Comma-separated list of URLs to probe each cycle.
    /// If unset, a built-in set of debrid + neutral endpoints is used.
    pub egress_watchdog_probe_urls: Option<String>,
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
            db_pool_size: env("DB_POOL_SIZE")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(10),
            db_pool_size_ro: env("DB_POOL_SIZE_RO")
                .ok()
                .and_then(|v| v.parse().ok()),
            db_pool_min: env("DB_POOL_MIN")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(2),
            db_acquire_timeout_secs: env("DB_ACQUIRE_TIMEOUT_SECS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(5),
            db_idle_timeout_secs: env("DB_IDLE_TIMEOUT_SECS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(600),
            db_max_lifetime_secs: env("DB_MAX_LIFETIME_SECS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(1800),
            db_statement_timeout_ms: env("DB_STATEMENT_TIMEOUT_MS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(60_000),
            db_idle_tx_timeout_ms: env("DB_IDLE_TX_TIMEOUT_MS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(60_000),
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
            poster_host_url: env("POSTER_HOST_URL")
                .ok()
                .filter(|s| !s.is_empty())
                .map(|s| s.trim_end_matches('/').to_string())
                .unwrap_or_else(|| {
                    env("HOST_URL")
                        .unwrap_or_else(|_| "http://localhost:8000".into())
                        .trim_end_matches('/')
                        .to_string()
                }),
            addon_name: env("ADDON_NAME")
                .unwrap_or_else(|_| "MediaFusion".into()),
            addon_version: env("VERSION")
                .unwrap_or_else(|_| env!("CARGO_PKG_VERSION").into()),
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
            jackett_live_title_search: env("JACKETT_LIVE_TITLE_SEARCH")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            background_search_enabled: env("BACKGROUND_SEARCH_ENABLED")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            background_max_process: env("BACKGROUND_MAX_PROCESS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(50),
            background_max_process_time: env("BACKGROUND_MAX_PROCESS_TIME")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(120),
            background_query_timeout: env("BACKGROUND_QUERY_TIMEOUT")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(30),
            background_search_interval_hours: env("BACKGROUND_SEARCH_INTERVAL_HOURS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(72),
            is_public_instance: env("IS_PUBLIC_INSTANCE")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            api_password: env("API_PASSWORD").ok().filter(|s| !s.is_empty()),
            enable_prometheus_metrics: env("ENABLE_PROMETHEUS_METRICS")
                .ok()
                .and_then(|v| v.parse().ok())
                .or_else(|| {
                    env("ENABLE_METRICS_ENDPOINT")
                        .ok()
                        .and_then(|v| v.parse().ok())
                })
                .unwrap_or(false),
            metrics_api_key: env("PROMETHEUS_METRICS_TOKEN")
                .ok()
                .filter(|s| !s.is_empty())
                .or_else(|| env("METRICS_BEARER_TOKEN").ok().filter(|s| !s.is_empty())),
            enable_torznab_api: env("ENABLE_TORZNAB_API")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            enable_rate_limit: env("ENABLE_RATE_LIMIT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(true),
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
            dmm_hashlist_repo_owner: env("DMM_HASHLIST_REPO_OWNER")
                .unwrap_or_else(|_| "debridmediamanager".into()),
            dmm_hashlist_repo_name: env("DMM_HASHLIST_REPO_NAME")
                .unwrap_or_else(|_| "hashlists".into()),
            dmm_hashlist_branch: env("DMM_HASHLIST_BRANCH")
                .unwrap_or_else(|_| "main".into()),
            dmm_hashlist_commits_per_run: env("DMM_HASHLIST_COMMITS_PER_RUN")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(20),
            dmm_hashlist_backfill_commits_per_run: env("DMM_HASHLIST_BACKFILL_COMMITS_PER_RUN")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(20),
            dmm_hashlist_github_token: env("DMM_HASHLIST_GITHUB_TOKEN")
                .ok()
                .filter(|s| !s.is_empty())
                .or_else(|| env("GITHUB_TOKEN").ok().filter(|s| !s.is_empty())),
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
            telegram_backup_channel_id: env("TELEGRAM_BACKUP_CHANNEL_ID").ok().filter(|s| !s.is_empty()),
            smtp_host: env("SMTP_HOST").ok().filter(|s| !s.is_empty()),
            smtp_port: env("SMTP_PORT").ok().and_then(|v| v.parse().ok()).unwrap_or(587),
            smtp_use_tls: env("SMTP_USE_TLS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(true),
            smtp_use_ssl: env("SMTP_USE_SSL")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(false),
            smtp_username: env("SMTP_USERNAME").ok().filter(|s| !s.is_empty()),
            smtp_password: env("SMTP_PASSWORD").ok().filter(|s| !s.is_empty()),
            smtp_from: env("SMTP_FROM_EMAIL")
                .unwrap_or_else(|_| "noreply@mediafusion.example.com".into()),
            convertkit_api_key: env("CONVERTKIT_API_KEY").ok().filter(|s| !s.is_empty()),
            convertkit_form_id: env("CONVERTKIT_FORM_ID").ok().filter(|s| !s.is_empty()),
            convertkit_newsletter_label: env("CONVERTKIT_NEWSLETTER_LABEL")
                .unwrap_or_else(|_| "Subscribe to our newsletter".into()),
            convertkit_newsletter_default_checked: env("CONVERTKIT_NEWSLETTER_DEFAULT_CHECKED")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            branding_svg: env("BRANDING_SVG").ok().filter(|s| !s.is_empty()),
            default_color_scheme: env("DEFAULT_COLOR_SCHEME")
                .unwrap_or_else(|_| "mediafusion".into()),
            sync_debrid_cache_streams: env("SYNC_DEBRID_CACHE_STREAMS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            store_stremthru_magnet_cache: env("STORE_STREMTHRU_MAGNET_CACHE")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(false),
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
            mdblist_api_key: env("MDBLIST_API_KEY").ok().filter(|s| !s.is_empty()),
            simkl_client_id: env("SIMKL_CLIENT_ID").ok().filter(|s| !s.is_empty()),
            simkl_client_secret: env("SIMKL_CLIENT_SECRET").ok().filter(|s| !s.is_empty()),
            disable_all_scheduler: env("DISABLE_ALL_SCHEDULER")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            youtube_api_key: env("YOUTUBE_API_KEY").ok().filter(|s| !s.is_empty()),
            tmdb_api_key: env("TMDB_API_KEY").ok().filter(|s| !s.is_empty()),
            tvdb_api_key: env("TVDB_API_KEY").ok().filter(|s| !s.is_empty()),
            imdb_cinemeta_fallback_enabled: env("IMDB_CINEMETA_FALLBACK_ENABLED")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(true),
            imdb_datasets_base_url: env("IMDB_DATASETS_BASE_URL")
                .unwrap_or_else(|_| "https://datasets.imdbws.com".into())
                .trim_end_matches('/')
                .to_string(),
            imdb_import_include_adult: env("IMDB_IMPORT_INCLUDE_ADULT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(false),
            imdb_import_datasets: env("IMDB_IMPORT_DATASETS")
                .ok()
                .filter(|s| !s.is_empty())
                .map(|s| {
                    s.split(',')
                        .map(|p| p.trim().to_ascii_lowercase())
                        .filter(|p| !p.is_empty())
                        .collect()
                })
                .unwrap_or_default(),
            metadata_primary_source: env("METADATA_PRIMARY_SOURCE")
                .unwrap_or_else(|_| "imdb".into())
                .to_lowercase(),
            anime_metadata_source_order: env("ANIME_METADATA_SOURCE_ORDER")
                .ok()
                .map(|s| {
                    s.split(',')
                        .map(|p| p.trim().to_lowercase())
                        .filter(|p| *p == "kitsu" || *p == "anilist")
                        .collect()
                })
                .filter(|v: &Vec<String>| !v.is_empty())
                .unwrap_or_else(|| vec!["kitsu".into(), "anilist".into()]),
            requests_proxy_url: env("REQUESTS_PROXY_URL").ok().filter(|s| !s.is_empty()),
            validate_m3u8_urls_liveness: env("VALIDATE_M3U8_URLS_LIVENESS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(true),
            discover_enabled: env("DISCOVER_ENABLED")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            discover_allow_server_key: env("DISCOVER_ALLOW_SERVER_KEY")
                .ok()
                .and_then(|v| v.parse().ok())
                // Auto-enable when TMDB_API_KEY is set and no explicit override given.
                .unwrap_or_else(|| env("TMDB_API_KEY").ok().filter(|s| !s.is_empty()).is_some()),
            image_upload_enabled: env("IMAGE_UPLOAD_ENABLED")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(false),
            images_dir: env("IMAGES_DIR").unwrap_or_else(|_| "./data/images".into()),
            image_storage_backend: env("IMAGE_STORAGE_BACKEND")
                .unwrap_or_else(|_| "local".into()),
            nzb_file_storage_backend: env("NZB_FILE_STORAGE_BACKEND")
                .unwrap_or_else(|_| "local".into()),
            nzb_dir: env("NZB_DIR").unwrap_or_else(|_| "./data/nzb".into()),
            nzb_download_url_expiry: env("NZB_DOWNLOAD_URL_EXPIRY")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(3600),
            s3_endpoint_url: env("S3_ENDPOINT_URL").ok().filter(|s| !s.is_empty()),
            s3_access_key_id: env("S3_ACCESS_KEY_ID").ok().filter(|s| !s.is_empty()),
            s3_secret_access_key: env("S3_SECRET_ACCESS_KEY").ok().filter(|s| !s.is_empty()),
            s3_bucket_name: env("S3_BUCKET_NAME").ok().filter(|s| !s.is_empty()),
            s3_region: env("S3_REGION").unwrap_or_else(|_| "auto".into()),
            enable_exception_tracking: env("ENABLE_EXCEPTION_TRACKING")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            exception_tracking_ttl: env("EXCEPTION_TRACKING_TTL")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(259200),
            exception_tracking_max_entries: env("EXCEPTION_TRACKING_MAX_ENTRIES")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(500),
            resources_dir: default_resources_dir(),
            frontend_dist_dir: default_frontend_dist_dir(),
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
            scraper_config_path: env("SCRAPER_CONFIG_PATH")
                .unwrap_or_else(|_| "../resources/json/scraper_config.json".into()),
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
            rd_blocked_substrings: env("RD_BLOCKED_SUBSTRINGS")
                .ok()
                .map(|s| {
                    s.split(',')
                        .map(|p| p.trim().to_lowercase())
                        .filter(|p| !p.is_empty())
                        .collect()
                })
                .unwrap_or_else(|| {
                    vec!["webrip".into(), "bdrip".into(), "hdrip".into(), "dvdrip".into()]
                }),
            rd_blocked_dot_pairs: env("RD_BLOCKED_DOT_PAIRS")
                .ok()
                .map(|s| {
                    s.split(',')
                        .map(|p| p.trim().to_lowercase())
                        .filter(|p| !p.is_empty())
                        .collect()
                })
                .unwrap_or_else(|| {
                    vec![
                        "bluray.x264".into(),
                        "hdtv.x264".into(),
                        "hdtv.xvid".into(),
                        "web.x264".into(),
                        "web.h264".into(),
                    ]
                }),
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
            requests_proxy_exclude_debrid_providers: env("REQUESTS_PROXY_EXCLUDE_DEBRID_PROVIDERS")
                .ok()
                .map(|s| {
                    // Accept either a JSON array or a comma-separated string.
                    if let Ok(v) = serde_json::from_str::<Vec<String>>(&s) {
                        v.into_iter().map(|x| x.to_lowercase()).collect()
                    } else {
                        s.split(',')
                            .map(|x| x.trim().to_lowercase())
                            .filter(|x| !x.is_empty())
                            .collect()
                    }
                })
                .unwrap_or_default(),
            requests_proxy_include_debrid_providers: env("REQUESTS_PROXY_INCLUDE_DEBRID_PROVIDERS")
                .ok()
                .map(|s| {
                    if let Ok(v) = serde_json::from_str::<Vec<String>>(&s) {
                        v.into_iter().map(|x| x.to_lowercase()).collect()
                    } else {
                        s.split(',')
                            .map(|x| x.trim().to_lowercase())
                            .filter(|x| !x.is_empty())
                            .collect()
                    }
                })
                .unwrap_or_default(),
            requests_proxy_non_debrid_enabled: env("REQUESTS_PROXY_NON_DEBRID_ENABLED")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            tcp_keepalive_secs: env("TCP_KEEPALIVE_SECS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(15),
            egress_watchdog_enabled: env("EGRESS_WATCHDOG_ENABLED")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(true),
            egress_watchdog_interval_secs: env("EGRESS_WATCHDOG_INTERVAL_SECS")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(30),
            egress_watchdog_fail_threshold: env("EGRESS_WATCHDOG_FAIL_THRESHOLD")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(5),
            egress_watchdog_probe_urls: env("EGRESS_WATCHDOG_PROBE_URLS")
                .ok().filter(|s| !s.is_empty()),
            poster_nsfw_enabled: env("POSTER_NSFW_ENABLED")
                .ok()
                .map(|v| matches!(v.to_lowercase().as_str(), "true" | "1" | "yes"))
                .unwrap_or(true),
            poster_nsfw_model_path: env("POSTER_NSFW_MODEL_PATH")
                .unwrap_or_else(|_| default_resources_dir() + "/nsfw_model.onnx"),
            poster_nsfw_threshold: env("POSTER_NSFW_THRESHOLD")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(0.7),
            poster_nsfw_model_version: env("POSTER_NSFW_MODEL_VERSION")
                .unwrap_or_else(|_| "v1".to_string()),
            poster_nsfw_scan_batch: env("POSTER_NSFW_SCAN_BATCH")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(100),
            rpdb_api_key: env("RPDB_API_KEY").ok().filter(|s| !s.is_empty()),
        }
    }

    /// True when all S3/R2 credentials required for object storage are present.
    pub fn s3_configured(&self) -> bool {
        self.s3_endpoint_url.as_ref().is_some_and(|s| !s.is_empty())
            && self
                .s3_access_key_id
                .as_ref()
                .is_some_and(|s| !s.is_empty())
            && self
                .s3_secret_access_key
                .as_ref()
                .is_some_and(|s| !s.is_empty())
            && self.s3_bucket_name.as_ref().is_some_and(|s| !s.is_empty())
    }

    /// Effective image storage backend (`s3` when S3 creds are set unless forced local).
    pub fn effective_image_storage_backend(&self) -> &str {
        if self.image_storage_backend.eq_ignore_ascii_case("local") {
            return "local";
        }
        if self.s3_configured() { "s3" } else { "local" }
    }

    /// Effective NZB storage backend.
    pub fn effective_nzb_storage_backend(&self) -> &str {
        if self.nzb_file_storage_backend.eq_ignore_ascii_case("s3") && self.s3_configured() {
            "s3"
        } else {
            "local"
        }
    }
}

/// Resolve a repo-relative directory whether the process cwd is repo root or `backend/`.
fn resolve_repo_relative_dir(env_key: &str, repo_relative: &str, marker: &str) -> String {
    if let Ok(v) = std::env::var(env_key) {
        if !v.is_empty() {
            return v;
        }
    }

    let from_manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join(repo_relative);
    if from_manifest.join(marker).exists() {
        return from_manifest.to_string_lossy().into_owned();
    }

    for candidate in [repo_relative, &format!("../{repo_relative}")] {
        if std::path::Path::new(candidate).join(marker).exists() {
            return candidate.to_string();
        }
    }

    from_manifest.to_string_lossy().into_owned()
}

fn default_resources_dir() -> String {
    resolve_repo_relative_dir("RESOURCES_DIR", "resources", "exceptions")
}

fn default_frontend_dist_dir() -> String {
    resolve_repo_relative_dir("FRONTEND_DIST_DIR", "clients/frontend/dist", "index.html")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_resources_dir_points_at_repo_resources() {
        let dir = default_resources_dir();
        assert!(
            std::path::Path::new(&dir)
                .join("exceptions")
                .join("daily_download_limit.mp4")
                .is_file(),
            "expected exception videos under {dir}/exceptions"
        );
    }
}
