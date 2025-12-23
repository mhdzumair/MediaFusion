# MediaFusion Environment Configuration Guide

This guide describes the environment variables available in MediaFusion for configuration. These settings control various aspects of the application, including database connections, service URLs, feature toggles, scheduling, and more. You can set these variables to customize MediaFusion according to your requirements.

## Core Application Settings

These settings define the basic configuration and identity of your MediaFusion instance.

- **addon_name** (default: `"MediaFusion"`): The name of the MediaFusion addon.
- **version** (default: `"1.0.0"`): The version of the MediaFusion addon.
- **description**: A brief description of the MediaFusion addon to show on Stremio Addon page.
- **branding_description** (default: `""`): Additional branding description text.
- **contact_email** (default: `"mhdzumair@gmail.com"`): The contact email for the MediaFusion addon.
- **host_url** (required): The URL where MediaFusion is hosted.
- **secret_key** (required): A 32-character secret key for securely signing the session. Must be exactly 32 characters long.
- **api_password** (required): The password for accessing the API endpoints.
- **logging_level** (default: `"INFO"`): The logging level of the application (DEBUG, INFO, WARNING, ERROR, CRITICAL).
- **logo_url**: The URL of the MediaFusion logo.
- **is_public_instance** (default: `False`): Set to `True` for community instances that don't require authentication except for `/scraper` endpoint.
- **poster_host_url** (default: Same as `host_url`): The URL where poster images are served from.
- **min_scraping_video_size** (default: `26214400`): Minimum video size in bytes (25 MB) for scraping.
- **metadata_primary_source** (default: `"imdb"`): Primary source for metadata. Options: "imdb" or "tmdb".

## Streaming Provider Settings

- **disabled_providers** (default: `[]`): List of disabled streaming providers. Available options:
  - "p2p": Peer-to-peer streaming
  - "realdebrid": RealDebrid service
  - "seedr": Seedr service
  - "debridlink": DebridLink service
  - "alldebrid": AllDebrid service
  - "offcloud": Offcloud service
  - "pikpak": PikPak service
  - "torbox": TorBox service
  - "premiumize": Premiumize service
  - "qbittorrent": qBittorrent client
  - "stremthru": StremThru service
  - "easydebrid": EasyDebrid service
  - "debrider": Debrider service

## Database and Cache Settings

### PostgreSQL Settings (Primary Database)
- **postgres_uri** (required): PostgreSQL connection URI for primary read-write operations.
  - Format: `postgresql+asyncpg://user:password@host:port/database`
  - Example: `postgresql+asyncpg://mediafusion:password@localhost:5432/mediafusion`
- **postgres_read_uri** (optional): PostgreSQL connection URI for read replica.
  - If not set, reads will use the primary `postgres_uri`.
  - Use this for scaling read operations in production.
- **db_max_connections** (default: `50`): Maximum database connections per pool.

### MongoDB Settings (Legacy/Migration)
- **mongo_uri** (required for migration): MongoDB connection URI.
  - Only needed during the migration process from MongoDB to PostgreSQL.
  - Can be removed after successful migration.

### Redis Settings
- **redis_url** (default: `"redis://redis-service:6379"`): Redis service URL for caching and tasks.
- **redis_max_connections** (default: `100`): Maximum Redis connections.
- **redis_retry_attempts** (default: `3`): Number of retry attempts for Redis operations.
- **redis_retry_delay** (default: `0.1`): Delay in seconds between retry attempts.
- **redis_connection_timeout** (default: `10`): Connection timeout in seconds.
- **redis_enable_circuit_breaker** (default: `True`): Enable circuit breaker for Redis operations.

## External Service Settings

- **requests_proxy_url**: Optional proxy URL for requests.
- **playwright_cdp_url** (default: `"ws://browserless:3000?blockAds=true&stealth=true"`): Playwright CDP service URL.
- **flaresolverr_url** (default: `"http://flaresolverr:8191/v1"`): FlareSolverr service URL.
- **tmdb_api_key**: TMDB API key for metadata fetching.

## Prowlarr Settings

- **is_scrap_from_prowlarr** (default: `True`): Enable/disable Prowlarr scraping.
- **prowlarr_url** (default: `"http://prowlarr-service:9696"`): Prowlarr service URL.
- **prowlarr_api_key**: Prowlarr API key.
- **prowlarr_live_title_search** (default: `True`): Enable live title search in Prowlarr.
- **prowlarr_background_title_search** (default: `True`): Enable background title search.
- **prowlarr_search_query_timeout** (default: `30`): Search query timeout in seconds.
- **prowlarr_search_interval_hour** (default: `72`): Search interval in hours.
- **prowlarr_immediate_max_process** (default: `10`): Max immediate processes.
- **prowlarr_immediate_max_process_time** (default: `15`): Max process time in seconds.
- **prowlarr_feed_scrape_interval_hour** (default: `3`): Feed scraping interval in hours.

## Torrentio Settings

- **is_scrap_from_torrentio** (default: `False`): Enable/disable Torrentio scraping.
- **torrentio_search_interval_days** (default: `3`): Search interval in days.
- **torrentio_url** (default: `"https://torrentio.strem.fun"`): Torrentio service URL.

## MediaFusion Settings

- **is_scrap_from_mediafusion** (default: `False`): Enable/disable MediaFusion scraping.
- **mediafusion_search_interval_days** (default: `3`): Search interval in days.
- **mediafusion_url** (default: `"https://mediafusion.elfhosted.com"`): MediaFusion service URL.
- **sync_debrid_cache_streams** (default: `True`): Enable syncing debrid cache streams.

## Zilean Settings

- **is_scrap_from_zilean** (default: `False`): Enable/disable Zilean scraping.
- **zilean_search_interval_hour** (default: `24`): Search interval in hours.
- **zilean_url** (default: `"https://zilean.elfhosted.com"`): Zilean service URL.

## BT4G Settings

- **is_scrap_from_bt4g** (default: `True`): Enable/disable BT4G scraping.
- **bt4g_url** (default: `"https://bt4gprx.com"`): BT4G service URL.
- **bt4g_search_interval_hour** (default: `72`): Search interval in hours.
- **bt4g_search_timeout** (default: `10`): Search timeout in seconds.
- **bt4g_immediate_max_process** (default: `15`): Max immediate processes.
- **bt4g_immediate_max_process_time** (default: `15`): Max process time in seconds.

## Jackett Settings

- **is_scrap_from_jackett** (default: `False`): Enable/disable Jackett scraping.
- **jackett_url** (default: `"http://jackett-service:9117"`): Jackett service URL.
- **jackett_api_key**: Jackett API key.
- **jackett_search_interval_hour** (default: `72`): Search interval in hours.
- **jackett_search_query_timeout** (default: `30`): Search query timeout in seconds.
- **jackett_immediate_max_process** (default: `10`): Max immediate processes.
- **jackett_immediate_max_process_time** (default: `15`): Max process time in seconds.
- **jackett_live_title_search** (default: `True`): Enable live title search.
- **jackett_background_title_search** (default: `True`): Enable background title search.
- **jackett_feed_scrape_interval_hour** (default: `3`): Feed scraping interval in hours.

## Premiumize Settings

- **premiumize_oauth_client_id**: Premiumize OAuth client ID.
- **premiumize_oauth_client_secret**: Premiumize OAuth client secret.

## Telegram Settings

- **telegram_bot_token**: Telegram bot token for notifications about contribution streams.
- **telegram_chat_id**: Telegram chat ID for notifications.

## Content Filtering Settings

- **adult_content_regex_keywords**: Regex pattern for filtering adult content.
- **adult_content_filter_in_torrent_title** (default: `True`): Enable adult content filtering in torrent titles.

## Feature Toggles

- **enable_rate_limit** (default: `False`): Enable/disable rate limiting.
- **validate_m3u8_urls_liveness** (default: `True`): Validate M3U8 URLs for liveness.
- **store_stremthru_magnet_cache** (default: `False`): Store StremThru magnet cache.
- **is_scrap_from_yts** (default: `True`): Enable/disable YTS scraping.
- **scrape_with_aka_titles** (default: `True`): Include alternative titles in scraping.
- **enable_fetching_torrent_metadata_from_p2p** (default: `True`): Enable fetching torrent metadata from P2P, Cautions: It may raise DMCA issues.

## Time-related Settings

- **meta_cache_ttl** (default: `1800`): Metadata cache TTL in seconds (30 minutes).
- **worker_max_tasks_per_child** (default: `20`): Max tasks per worker child process.

## Scheduler Settings

### Global Settings
- **disable_all_scheduler** (default: `False`): Disable all schedulers.
- **background_search_interval_hours** (default: `72`): Background search interval in hours.
- **background_search_crontab** (default: `"*/5 * * * *"`): Background search schedule.

### Individual Scheduler Settings
Each scheduler has a crontab expression and disable flag:

- **tamilmv_scheduler_crontab** (default: `"0 */3 * * *"`)
- **tamil_blasters_scheduler_crontab** (default: `"0 */6 * * *"`)
- **formula_tgx_scheduler_crontab** (default: `"*/30 * * * *"`)
- **nowmetv_scheduler_crontab** (default: `"0 0 * * *"`)
- **nowsports_scheduler_crontab** (default: `"0 10 * * *"`)
- **tamilultra_scheduler_crontab** (default: `"0 8 * * *"`)
- **validate_tv_streams_in_db_crontab** (default: `"0 */6 * * *"`)
- **sport_video_scheduler_crontab** (default: `"*/20 * * * *"`)
- **dlhd_scheduler_crontab** (default: `"25 * * * *"`)
- **motogp_tgx_scheduler_crontab** (default: `"0 5 * * *"`)
- **update_seeders_crontab** (default: `"0 0 * * *"`)
- **arab_torrents_scheduler_crontab** (default: `"0 0 * * *"`)
- **wwe_tgx_scheduler_crontab** (default: `"10 */3 * * *"`)
- **ufc_tgx_scheduler_crontab** (default: `"30 */3 * * *"`)
- **movies_tv_tgx_scheduler_crontab** (default: `"0 * * * *"`)
- **prowlarr_feed_scraper_crontab** (default: `"0 */3 * * *"`)
- **jackett_feed_scraper_crontab** (default: `"0 */3 * * *"`)
- **cleanup_expired_scraper_task_crontab** (default: `"0 * * * *"`)
- **cleanup_expired_cache_task_crontab** (default: `"0 0 * * *"`)

Each scheduler can be disabled individually using its corresponding `disable_*_scheduler` setting.

## Local Development Settings

- **use_config_source** (default: `remote`): Use the remote scraper configuration file or local source.

### How to Configure

You can configure these settings either through environment variables in your deployment or through a `.env` file:

#### Configuration for k8s
```yaml
env:
  - name: POSTGRES_URI
    value: "postgresql+asyncpg://mediafusion:password@postgres:5432/mediafusion"
  - name: POSTGRES_READ_URI
    value: "postgresql+asyncpg://mediafusion:password@postgres-read:5432/mediafusion"  # Optional
  - name: DB_MAX_CONNECTIONS
    value: "100"
  # Add other configurations as needed
```

#### Configuration for Docker Compose
Create or modify `.env` file:
```env
POSTGRES_URI=postgresql+asyncpg://mediafusion:password@postgres:5432/mediafusion
POSTGRES_READ_URI=postgresql+asyncpg://mediafusion:password@postgres-read:5432/mediafusion  # Optional
DB_MAX_CONNECTIONS=100
# Add other configurations as needed
```

> [!TIP]
> For scheduler crontabs, you can use [crontab.guru](https://crontab.guru/) to generate and validate crontab expressions.
