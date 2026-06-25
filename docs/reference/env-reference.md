# Environment Variables

Complete reference for all MediaFusion environment variables. Variables are read from the environment or from a `.env` file in the working directory.

!!! tip "Search this page"
    Use `ctrl+f` / `cmd+f` in your browser to search for a specific variable name.

!!! note "Case-insensitive"
    Variable names are case-insensitive — `SECRET_KEY` and `secret_key` both work.

---

## Required Variables

These 4 variables must be set before MediaFusion will start:

| Variable | Example | Description |
|---|---|---|
| `SECRET_KEY` | *(32+ char random string)* | AES-256 key for encrypting user profile data in manifest URLs. Generate with `openssl rand -hex 16`. |
| `POSTGRES_URI` | `postgresql://user:pass@host:5432/db` | Primary PostgreSQL connection string. Both `postgresql://` and `postgresql+asyncpg://` formats are accepted. |
| `HOST_URL` | `https://mediafusion.example.com` | Public base URL of your instance. Used to build all manifest and stream URLs. |
| `API_PASSWORD` | *(strong password)* | Password protecting admin endpoints (`/scraper`, admin API). Set on private instances. |

`CONTACT_EMAIL` is strongly recommended (shown in addon metadata) but not strictly required.

---

## Core Application

| Variable | Default | Description |
|---|---|---|
| `ADDON_NAME` | `MediaFusion` | Name shown in Stremio/Kodi addon listings. |
| `VERSION` | `Cargo.toml` package version | Version string shown in manifest metadata. |
| `ADDON_DESCRIPTION` | *(built-in)* | Addon description. Env var: `ADDON_DESCRIPTION`. |
| `ADDON_LOGO` | *(MediaFusion CDN URL)* | URL of the logo shown in Stremio. |
| `CONTACT_EMAIL` | — | Contact email shown in addon metadata. |
| `BRANDING_DESCRIPTION` | *(built-in)* | Branding text shown on the home page (may contain HTML). |
| `IS_PUBLIC_INSTANCE` | `false` | When `true`, disables API password enforcement everywhere. Use for fully open community instances. |
| `MIN_SCRAPING_VIDEO_SIZE` | `26214400` | Minimum file size in bytes (25 MB) to consider a torrent file a valid video. |
| `DISABLED_CONTENT_TYPES` | `[]` | JSON array of content types to disable globally: `magnet`, `torrent`, `nzb`, `iptv`, `youtube`, `http`, `acestream`, `telegram`. |
| `DISABLED_PROVIDERS` | `[]` | JSON array of provider names to remove from the Configure UI: `p2p`, `realdebrid`, `seedr`, `debridlink`, `alldebrid`, `offcloud`, `pikpak`, `torbox`, `premiumize`, `qbittorrent`, `stremthru`, `easydebrid`, `debrider`. |
| `MAX_STREAMING_PROVIDERS_PER_PROFILE` | `5` | Maximum number of providers a user profile can configure. |
| `PROVIDER_SIGNUP_LINKS` | *(built-in)* | JSON object of provider → list of signup URLs. Appended to built-in defaults. |
| `STREAM_RS_PORT` | `8000` | HTTP port the Rust API server listens on. |
| `REQUEST_TIMEOUT` | `120` | Request timeout in seconds for `/stream/` routes. Increase if live scrapers are slow. |
| `SCRAPER_CONFIG_PATH` | `resources/scraper_config.yaml` | Path to the scraper configuration YAML file. |

---

## Database

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_URI` | **required** | Primary read-write PostgreSQL connection string. |
| `POSTGRES_READ_URI` | — | Optional read-replica URI. Read queries route here when set. |
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis connection URL for cache, task queue, and rate limiting. |

---

## Caching

| Variable | Default | Description |
|---|---|---|
| `META_CACHE_TTL_SECONDS` | `1800` | Redis TTL for metadata (meta/catalog) responses (seconds). |
| `CATALOG_CACHE_TTL_SECONDS` | `1800` | Redis TTL for catalog listing responses (seconds). |
| `STREAM_RAW_REDIS_CACHE_TTL_SECONDS` | `900` | Redis TTL for raw stream blobs (seconds). |

---

## Authentication & Security

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | **required** | AES-256 encryption key for user profile data. Changing this invalidates all existing user manifests. |
| `API_PASSWORD` | — | Password for admin endpoints. Leave unset on fully public instances (`IS_PUBLIC_INSTANCE=true`). |
| `IS_PUBLIC_INSTANCE` | `false` | Disable all API password checks. |
| `ENABLE_TORZNAB_API` | `true` | Expose the Torznab feed endpoint at `/torznab`. |
| `ENABLE_NZB_FILE_IMPORT` | `true` | Allow NZB file imports via the web UI. |

---

## Metrics & Observability

| Variable | Default | Description |
|---|---|---|
| `ENABLE_PROMETHEUS_METRICS` | `false` | Expose a Prometheus-compatible `/api/v1/metrics` endpoint. |
| `PROMETHEUS_METRICS_TOKEN` | — | Bearer token required to scrape `/api/v1/metrics`. When unset, the endpoint is open. |
| `ENABLE_EXCEPTION_TRACKING` | `false` | Track recent exceptions in Redis for admin visibility. |
| `EXCEPTION_TRACKING_TTL` | `259200` | TTL in seconds for exception records (3 days). |
| `EXCEPTION_TRACKING_MAX_ENTRIES` | `500` | Maximum distinct exceptions to keep in Redis. |

---

## Scraper Endpoints

| Variable | Default | Description |
|---|---|---|
| `PROWLARR_URL` | — | Base URL of your Prowlarr instance (e.g. `http://prowlarr:9696`). |
| `PROWLARR_API_KEY` | — | API key from Prowlarr → Settings → General. |
| `JACKETT_URL` | — | Base URL of your Jackett instance. |
| `JACKETT_API_KEY` | — | Jackett API key. |
| `ZILEAN_URL` | `https://zilean.elfhosted.com` | Zilean DMM base URL. |
| `TORRENTIO_URL` | `https://torrentio.strem.fun` | Torrentio base URL. |
| `MEDIAFUSION_URL` | `https://mediafusion.elfhosted.com` | Peer MediaFusion instance URL for cross-instance aggregation. |
| `MEDIAFUSION_SECRET_STR` | — | Optional secret string for authenticated scraping from the peer instance. |
| `BYPARR_URL` | — | [Byparr](https://github.com/ThePhaseless/Byparr) (FlareSolverr-compatible) base URL. When set, Cloudflare-protected indexers are fetched via Byparr. |
| `BROWSERLESS_URL` | — | Browserless v2 base URL (e.g. `http://browserless:3000`) for JS-heavy scrapers. |
| `REQUESTS_PROXY_URL` | — | HTTP proxy for all outbound scraper requests (and debrid API calls). Set to your gost/WARP tunnel URL. |
| `REQUESTS_PROXY_EXCLUDE_DEBRID_PROVIDERS` | `[]` | Comma-separated (or JSON array) list of debrid provider IDs that bypass `REQUESTS_PROXY_URL` and connect directly. Ignored when `REQUESTS_PROXY_INCLUDE_DEBRID_PROVIDERS` is set. Valid IDs: `realdebrid`, `seedr`, `debridlink`, `alldebrid`, `offcloud`, `pikpak`, `torbox`, `premiumize`, `stremthru`, `easydebrid`, `debrider`. |
| `REQUESTS_PROXY_INCLUDE_DEBRID_PROVIDERS` | `[]` | When non-empty, **only** these debrid provider IDs are routed through `REQUESTS_PROXY_URL`; all others connect directly. Takes precedence over `REQUESTS_PROXY_EXCLUDE_DEBRID_PROVIDERS`. Same format and valid IDs as the exclude list. |
| `REQUESTS_PROXY_NON_DEBRID_ENABLED` | `true` | When `false`, general (non-debrid-provider) HTTP calls — catalog browsing, indexer tests, content discovery — bypass `REQUESTS_PROXY_URL` and connect directly. Debrid provider routing is unaffected. |

---

## HTTP Client & Egress

| Variable | Default | Description |
|---|---|---|
| `TCP_KEEPALIVE_SECS` | `15` | TCP keepalive probe interval (seconds) for all outbound HTTP clients. Keeps NAT/conntrack mappings alive through tunnel proxies (gost, WARP) and enables the OS to detect stale sockets before they are reused. |
| `EGRESS_WATCHDOG_ENABLED` | `true` | Enable the egress self-heal watchdog. When all probe targets fail for `EGRESS_WATCHDOG_FAIL_THRESHOLD` consecutive cycles the process exits so k8s can restart the pod and re-establish the tunnel. |
| `EGRESS_WATCHDOG_INTERVAL_SECS` | `30` | Seconds between watchdog probe cycles. |
| `EGRESS_WATCHDOG_FAIL_THRESHOLD` | `5` | Consecutive fully-failed cycles before the watchdog triggers a restart (~2.5 min at the default interval). |
| `EGRESS_WATCHDOG_PROBE_URLS` | *(built-in)* | Comma-separated list of URLs probed each cycle. Defaults to `api.real-debrid.com`, `api.alldebrid.com`, and `1.1.1.1`. A cycle counts as failed only when **all** targets return a transport error — a single-provider outage or HTTP 4xx/5xx never trips the watchdog. |

---

## Scraper Enable Flags

| Variable | Default | Description |
|---|---|---|
| `IS_SCRAP_FROM_PROWLARR` | `true` | Enable Prowlarr as a stream source. |
| `IS_SCRAP_FROM_JACKETT` | `false` | Enable Jackett as a stream source. |
| `IS_SCRAP_FROM_TORZNAB` | `true` | Enable direct Torznab endpoints as stream sources. |
| `IS_SCRAP_FROM_PUBLIC_INDEXERS` | `true` | Enable built-in public torrent indexer scrapers (1337x, TPB, YTS, etc.). |
| `IS_SCRAP_FROM_PUBLIC_USENET_INDEXERS` | `true` | Enable built-in public Usenet indexer scrapers. |
| `IS_SCRAP_FROM_ZILEAN` | `false` | Fetch cached streams from Zilean DMM. |
| `IS_SCRAP_FROM_TORRENTIO` | `false` | Fetch streams from Torrentio. |
| `IS_SCRAP_FROM_MEDIAFUSION` | `false` | Fetch streams from the peer MediaFusion instance. |
| `IS_SCRAP_FROM_DMM_HASHLIST` | `false` | Ingest torrent hashes from the DMM GitHub hashlist. |
| `PUBLIC_INDEXERS_LIVE_SEARCH_SITES` | — | Comma-separated list of public indexer keys to use for live search (e.g. `x1337,nyaa`). Empty = all enabled indexers. |

---

## Live Search

| Variable | Default | Description |
|---|---|---|
| `PROWLARR_LIVE_TITLE_SEARCH` | `true` | Include a title search in live Prowlarr queries. |
| `JACKETT_LIVE_TITLE_SEARCH` | `true` | Include a title search in live Jackett queries. |
| `PROWLARR_IMMEDIATE_MAX_PROCESS` | `30` | Max results to keep per indexer from a live Prowlarr search. |
| `PROWLARR_IMMEDIATE_MAX_PROCESS_TIME` | `30` | Timeout in seconds for the live Prowlarr search. |
| `PROWLARR_SEARCH_QUERY_TIMEOUT` | `30` | Per-request HTTP timeout for Prowlarr calls (seconds). |
| `JACKETT_IMMEDIATE_MAX_PROCESS` | `30` | Max results from a live Jackett search. |
| `JACKETT_IMMEDIATE_MAX_PROCESS_TIME` | `30` | Timeout in seconds for the live Jackett search. |
| `JACKETT_SEARCH_QUERY_TIMEOUT` | `30` | Per-request HTTP timeout for Jackett calls (seconds). |

---

## Background Search

Background search queues items seen during live requests for periodic re-scraping.

| Variable | Default | Description |
|---|---|---|
| `BACKGROUND_SEARCH_ENABLED` | `true` | Enable background re-scraping of previously requested titles. |
| `BACKGROUND_MAX_PROCESS` | `50` | Max results per indexer during a background search run. |
| `BACKGROUND_MAX_PROCESS_TIME` | `120` | Time budget in seconds for background Prowlarr/Jackett scrapes. |
| `BACKGROUND_QUERY_TIMEOUT` | `30` | Per-request HTTP timeout for background scraper calls (seconds). |
| `BACKGROUND_SEARCH_INTERVAL_HOURS` | `72` | Minimum hours between background re-scrapes for the same item. |

---

## Search Cache TTLs (Re-search Intervals)

These control how long scraped results are considered fresh before re-querying.

| Variable | Default | Description |
|---|---|---|
| `PROWLARR_SEARCH_INTERVAL_HOUR` | `72` | Hours between Prowlarr re-searches for a given title. |
| `JACKETT_SEARCH_INTERVAL_HOUR` | `72` | Hours between Jackett re-searches. |
| `ZILEAN_SEARCH_INTERVAL_HOUR` | `24` | Hours between Zilean re-searches. |
| `TORRENTIO_SEARCH_INTERVAL_DAYS` | `3` | Days between Torrentio re-searches. |
| `MEDIAFUSION_SEARCH_INTERVAL_DAYS` | `3` | Days between peer MediaFusion re-searches. |
| `PUBLIC_INDEXERS_SEARCH_INTERVAL_HOUR` | `48` | Hours between public indexer re-searches. |
| `PUBLIC_USENET_INDEXERS_SEARCH_INTERVAL_HOUR` | `48` | Hours between public Usenet indexer re-searches. |
| `DMM_HASHLIST_SYNC_INTERVAL_HOUR` | `6` | Hours between DMM hashlist incremental syncs. |
| `TORBOX_SEARCH_TTL` | *(prowlarr interval)* | Override cache TTL for Torbox searches (seconds). Defaults to `PROWLARR_SEARCH_INTERVAL_HOUR * 3600`. |

---

## DMM Hashlist

| Variable | Default | Description |
|---|---|---|
| `IS_SCRAP_FROM_DMM_HASHLIST` | `false` | Enable DMM hashlist ingestion. |
| `DMM_HASHLIST_REPO_OWNER` | `debridmediamanager` | GitHub repo owner for hashlist. |
| `DMM_HASHLIST_REPO_NAME` | `hashlists` | GitHub repo name for hashlist. |
| `DMM_HASHLIST_BRANCH` | `main` | Git branch to read commits from. |
| `DMM_HASHLIST_COMMITS_PER_RUN` | `20` | Max new commits to process per incremental run. |
| `DMM_HASHLIST_BACKFILL_COMMITS_PER_RUN` | `20` | Max backfill commits per run. |
| `DMM_HASHLIST_GITHUB_TOKEN` | — | GitHub token for API authentication. Falls back to `GITHUB_TOKEN`. |

---

## Metadata

| Variable | Default | Description |
|---|---|---|
| `METADATA_PRIMARY_SOURCE` | `imdb` | Primary metadata source: `imdb` or `tmdb`. |
| `TMDB_API_KEY` | — | TMDB API key. Required when `METADATA_PRIMARY_SOURCE=tmdb` or Discover is enabled. |
| `TVDB_API_KEY` | — | TVDB API key for series metadata. |
| `IMDB_CINEMETA_FALLBACK_ENABLED` | `true` | Fall back to v3-cinemeta.strem.io when IMDb lookup fails. |
| `IMDB_DATASETS_BASE_URL` | *(official URL)* | Base URL for IMDb non-commercial dataset downloads. |
| `IMDB_IMPORT_INCLUDE_ADULT` | `false` | Include adult titles in IMDb dataset import. |
| `IMDB_IMPORT_DATASETS` | *(all)* | Comma-separated list of dataset keys to import. Empty = all. |
| `ANIME_METADATA_SOURCE_ORDER` | `kitsu,anilist` | Ordered list of anime metadata providers. |
| `DISCOVER_ENABLED` | `true` | Enable the Discover catalog section. |
| `DISCOVER_ALLOW_SERVER_KEY` | `false` | Use the server's TMDB key for users who haven't set their own. |

---

## IPTV

| Variable | Default | Description |
|---|---|---|
| `ENABLE_IPTV_IMPORT` | `true` | Allow users to import M3U/Xtream/Stalker IPTV sources. |
| `ALLOW_PUBLIC_IPTV_SHARING` | `false` | Allow imported IPTV playlists to be shared publicly. |

---

## Premiumize OAuth

| Variable | Default | Description |
|---|---|---|
| `PREMIUMIZE_OAUTH_CLIENT_ID` | — | OAuth client ID from [premiumize.me/registerclient](https://www.premiumize.me/registerclient). |
| `PREMIUMIZE_OAUTH_CLIENT_SECRET` | — | OAuth client secret. |

---

## NZBDav (Default)

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_NZBDAV_URL` | — | Operator-configured NzbDAV URL. Auto-injected into new user profiles when set. |
| `DEFAULT_NZBDAV_API_KEY` | — | NzbDAV API key. |

---

## Telegram Bot

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Telegram Bot API token from @BotFather. |
| `TELEGRAM_BOT_USERNAME` | — | Bot username (without @). |
| `TELEGRAM_WEBHOOK_SECRET_TOKEN` | — | Webhook secret for validating Telegram webhook requests. |
| `TELEGRAM_CHAT_ID` | — | Chat ID for moderation notifications. |
| `TELEGRAM_BACKUP_CHANNEL_ID` | — | Backup channel for storing contributed video files. |

### Telegram Scraping (User API)

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_API_ID` | — | Telegram API ID (from my.telegram.org). |
| `TELEGRAM_API_HASH` | — | Telegram API hash. |
| `TELEGRAM_GRAMMERS_SESSION` | — | Grammers session string for the Telegram scraper. |
| `TELEGRAM_SCRAPING_CHANNELS` | — | Comma-separated list of channel usernames/IDs to scrape. |
| `TELEGRAM_SCRAPE_MESSAGE_LIMIT` | `100` | Max messages to fetch per channel per scrape run. |

---

## SMTP / Email

| Variable | Default | Description |
|---|---|---|
| `SMTP_HOST` | — | SMTP server hostname. Required for email verification/reset. |
| `SMTP_PORT` | `587` | SMTP server port. |
| `SMTP_TLS_ENABLED` | `true` | Use TLS for SMTP. Set `false` for internal relay on port 25. |
| `SMTP_USERNAME` | — | SMTP login username. |
| `SMTP_PASSWORD` | — | SMTP login password. |
| `SMTP_FROM_EMAIL` | `noreply@mediafusion.example.com` | From address for outgoing emails. |

---

## Third-Party Integrations

| Variable | Default | Description |
|---|---|---|
| `TRAKT_CLIENT_ID` | — | Trakt OAuth client ID (enables Trakt watchlist sync). |
| `TRAKT_CLIENT_SECRET` | — | Trakt OAuth client secret. |
| `MDBLIST_API_KEY` | — | Server-level MDBList API key for list ingestion into the media catalog. |
| `SIMKL_CLIENT_ID` | — | Simkl OAuth client ID. |
| `SIMKL_CLIENT_SECRET` | — | Simkl OAuth client secret. |
| `SYNC_DEBRID_CACHE_STREAMS` | `false` | Sync cached stream data from debrid provider APIs. |

---

## Image Upload

| Variable | Default | Description |
|---|---|---|
| `IMAGE_UPLOAD_ENABLED` | `false` | Enable local image upload endpoint. |
| `IMAGES_DIR` | `./data/images` | Directory to store uploaded images. |

---

## Static Files

| Variable | Default | Description |
|---|---|---|
| `RESOURCES_DIR` | `resources/` | Directory containing static resources served at `/static`. |
| `FRONTEND_DIST_DIR` | `clients/frontend/dist` | Path to the built React SPA served at `/app`. |

---

## Public Indexer Health Gates

These variables control automatic circuit-breaking for public indexer sources that perform poorly.

| Variable | Default | Description |
|---|---|---|
| `PUBLIC_INDEXERS_SOURCE_HEALTH_GATES_ENABLED` | `true` | Enable health-based circuit breakers for public indexers. |
| `PUBLIC_INDEXERS_SOURCE_HEALTH_MIN_SAMPLES` | *(built-in)* | Minimum request samples before health gate activates. |
| `PUBLIC_INDEXERS_SOURCE_MIN_SUCCESS_RATE` | *(built-in)* | Minimum success rate (0–1) to keep a source open. |
| `PUBLIC_INDEXERS_SOURCE_MAX_TIMEOUT_RATE` | *(built-in)* | Maximum timeout rate before tripping the circuit. |
| `PUBLIC_INDEXERS_SOURCE_HEALTH_COUNTER_SOFT_CAP` | *(built-in)* | Rolling window cap for health counters. |
| `PUBLIC_INDEXERS_SOURCE_HEALTH_DECAY_FACTOR` | *(built-in)* | Exponential decay factor for health counters. |
| `PUBLIC_INDEXERS_SOURCE_HEALTH_RECOVERY_SUCCESS_STREAK` | *(built-in)* | Consecutive successes needed to recover a tripped circuit. |
| `PUBLIC_INDEXERS_SOURCE_HEALTH_SCOPE_MODE` | *(built-in)* | Health scope mode. |
| `PUBLIC_INDEXERS_SOURCE_HEALTH_SCOPE` | *(built-in)* | Health scope value. |
| `PUBLIC_INDEXERS_SOURCE_HEALTH_METRICS_TTL_SECONDS` | *(built-in)* | TTL for health metric counters in Redis. |

---

## Scheduler: Global

| Variable | Default | Description |
|---|---|---|
| `DISABLE_ALL_SCHEDULER` | `false` | Disable all background scheduling. Useful during development. |
| `BACKGROUND_SEARCH_CRONTAB` | *(built-in)* | Crontab for the background search job. |
| `INTEGRATION_SYNC_CRONTAB` | *(built-in)* | Crontab for syncing third-party integrations (Trakt, Simkl). |
| `DISABLE_INTEGRATION_SYNC_SCHEDULER` | `false` | Disable the integration sync scheduler. |
| `UPDATE_SEEDERS_CRONTAB` | *(built-in)* | Crontab for updating torrent seeder counts. |
| `DISABLE_UPDATE_SEEDERS` | `false` | Disable seeder count updates. |
| `VALIDATE_TV_STREAMS_IN_DB_CRONTAB` | *(built-in)* | Crontab for validating TV stream URLs. |
| `DISABLE_VALIDATE_TV_STREAMS_IN_DB` | `false` | Disable TV stream validation. |
| `CLEANUP_EXPIRED_SCRAPER_TASK_CRONTAB` | *(built-in)* | Crontab for cleaning expired scraper task records. |
| `CLEANUP_EXPIRED_CACHE_TASK_CRONTAB` | *(built-in)* | Crontab for cleaning expired Redis cache entries. |

---

## Scheduler: Content Scrapers

Each content scraper has a `_CRONTAB` and a `DISABLE_` toggle. Only the most commonly adjusted are listed; the pattern is consistent across all scrapers.

| Scraper | Enable crontab | Disable flag |
|---|---|---|
| TamilMV | `TAMILMV_SCHEDULER_CRONTAB` *(0 */3 * * *)* | `DISABLE_TAMILMV_SCHEDULER` |
| Tamil Blasters | `TAMIL_BLASTERS_SCHEDULER_CRONTAB` *(0 */6 * * *)* | `DISABLE_TAMIL_BLASTERS_SCHEDULER` |
| Formula (ext) | `FORMULA_EXT_SCHEDULER_CRONTAB` *(*/30 * * * *)* | `DISABLE_FORMULA_EXT_SCHEDULER` |
| MotoGP (ext) | `MOTOGP_EXT_SCHEDULER_CRONTAB` *(0 5 * * *)* | `DISABLE_MOTOGP_EXT_SCHEDULER` |
| WWE (ext) | `WWE_EXT_SCHEDULER_CRONTAB` | `DISABLE_WWE_EXT_SCHEDULER` |
| UFC (ext) | `UFC_EXT_SCHEDULER_CRONTAB` | `DISABLE_UFC_EXT_SCHEDULER` |
| Movies/TV (ext) | `MOVIES_TV_EXT_SCHEDULER_CRONTAB` | `DISABLE_MOVIES_TV_EXT_SCHEDULER` |
| NowMeTV | `NOWMETV_SCHEDULER_CRONTAB` | `DISABLE_NOWMETV_SCHEDULER` |
| NowSports | `NOWSPORTS_SCHEDULER_CRONTAB` | `DISABLE_NOWSPORTS_SCHEDULER` |
| TamilUltra | `TAMILULTRA_SCHEDULER_CRONTAB` | `DISABLE_TAMILULTRA_SCHEDULER` |
| Sport Video | `SPORT_VIDEO_SCHEDULER_CRONTAB` | `DISABLE_SPORT_VIDEO_SCHEDULER` |
| DLHD | `DLHD_SCHEDULER_CRONTAB` | `DISABLE_DLHD_SCHEDULER` |
| Arab Torrents | `ARAB_TORRENTS_SCHEDULER_CRONTAB` | `DISABLE_ARAB_TORRENTS_SCHEDULER` |
| 1337x | `X1337_SCHEDULER_CRONTAB` | *(use `IS_SCRAP_FROM_PUBLIC_INDEXERS`)* |
| The Pirate Bay | `THEPIRATEBAY_SCHEDULER_CRONTAB` | `DISABLE_THEPIRATEBAY_SCHEDULER` |
| Rutor | `RUTOR_SCHEDULER_CRONTAB` | `DISABLE_RUTOR_SCHEDULER` |
| LimeTorrents | `LIMETORRENTS_SCHEDULER_CRONTAB` | `DISABLE_LIMETORRENTS_SCHEDULER` |
| YTS | `YTS_SCHEDULER_CRONTAB` | `DISABLE_YTS_SCHEDULER` |
| BT4G | `BT4G_SCHEDULER_CRONTAB` | *(built-in default)* |
| EZTV RSS | `EZTV_RSS_SCHEDULER_CRONTAB` | `DISABLE_EZTV_RSS_SCHEDULER` |
| Nyaa | `NYAA_SCHEDULER_CRONTAB` | `DISABLE_NYAA_SCHEDULER` |
| AnimeTosho | `ANIMETOSHO_SCHEDULER_CRONTAB` | `DISABLE_ANIMETOSHO_SCHEDULER` |
| SubsPlease | `SUBSPLEASE_SCHEDULER_CRONTAB` | `DISABLE_SUBSPLEASE_SCHEDULER` |
| AnimePahe | `ANIMEPAHE_SCHEDULER_CRONTAB` | `DISABLE_ANIMEPAHE_SCHEDULER` |
| BT52 | `BT52_SCHEDULER_CRONTAB` | *(built-in default)* |
| UIndex | `UINDEX_SCHEDULER_CRONTAB` | `DISABLE_UINDEX_SCHEDULER` |
| Prowlarr feed | `PROWLARR_FEED_SCRAPER_CRONTAB` | `DISABLE_PROWLARR_FEED_SCRAPER` |
| Jackett feed | `JACKETT_FEED_SCRAPER_CRONTAB` | `DISABLE_JACKETT_FEED_SCRAPER` |
| RSS feeds | `RSS_FEED_SCRAPER_CRONTAB` | `DISABLE_RSS_FEED_SCRAPER` |
| DMM hashlist | `DMM_HASHLIST_SCRAPER_CRONTAB` | `DISABLE_DMM_HASHLIST_SCRAPER` |
| YouTube | `YOUTUBE_BACKGROUND_SCRAPER_CRONTAB` | `DISABLE_YOUTUBE_BACKGROUND_SCRAPER` |
| AceStream | `ACESTREAM_BACKGROUND_SCRAPER_CRONTAB` | `DISABLE_ACESTREAM_BACKGROUND_SCRAPER` |
| Telegram | `TELEGRAM_BACKGROUND_SCRAPER_CRONTAB` | `DISABLE_TELEGRAM_BACKGROUND_SCRAPER` |
