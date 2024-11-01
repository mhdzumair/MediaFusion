# MediaFusion Environment Configuration Guide

This guide describes the environment variables available in MediaFusion for configuration. These settings control various aspects of the application, including database connections, service URLs, feature toggles, scheduling, and more. You can set these variables to customize MediaFusion according to your requirements.

## Core Application Settings

These settings define the basic configuration and identity of your MediaFusion instance.

- **addon_name** (default: `"MediaFusion"`): The name of the MediaFusion addon. You can customize this value to identify the addon.
- **version** : The version of the MediaFusion addon.
- **description** : A brief description of the MediaFusion addon to show on stremio Addon page.
- **contact_email** : The contact email for the MediaFusion addon to show on stremio Addon page.
- **host_url** (required): The URL where MediaFusion is hosted.
- **secret_key** (required): A 32-character secret key for securely signing the session. Must be exactly 32 characters long.
- **api_password** (required): The password for accessing the API endpoints.
- **git_rev** (default: `"stable"`): The Git revision or version of the application.
- **logging_level** (default: `"INFO"`): The logging level of the application. Valid options are typically DEBUG, INFO, WARNING, ERROR, and CRITICAL.
- **logo_url** (default: GitHub RAW Addon URL): The URL of the MediaFusion logo.
- **is_public_instance** (default: `False`): Set to `True` for community instances that do not require authentication to access the data but protect the `/scraper` endpoint and uploading live TV data endpoints.

## Database and Cache Settings

These settings control the database and caching behavior of MediaFusion.

- **mongo_uri** (required): The MongoDB URI connection string.
- **db_max_connections** (default: 50): The maximum number of connections to the database.
- **redis_url** (default: `"redis://redis-service:6379"`): The Redis service URL used for caching and task queuing.

## External Service URLs

These URLs define the locations of various external services used by MediaFusion.

- **poster_host_url** (default: Use the Host URL value): The URL where poster images are served from. Use the same value as `host_url` if posters are served from the same location.
- **scraper_proxy_url**: The proxy URL for the scraper, if any.
- **torrentio_url** (default: `"https://torrentio.strem.fun"`): The Torrentio / KightCrawler URL.
- **playwright_cdp_url** (default: `"ws://browserless:3000?blockAds=true&stealth=true"`): The URL for the Playwright CDP (Chrome DevTools Protocol) service.
- **flaresolverr_url** (default: `"http://flaresolverr:8191/v1"`): The URL for the FlareSolverr service.

## Prowlarr Settings

These settings are specific to the Prowlarr integration.

- **prowlarr_url** (default: `"http://prowlarr-service:9696"`): The Prowlarr service URL.
- **prowlarr_api_key**: The API key for Prowlarr authentication.
- **prowlarr_live_title_search** (default: `False`): Enable or disable live title search in Prowlarr. If False, search movie/series by title in background worker.
- **prowlarr_background_title_search** (default: `True`): Enable or disable background title search in Prowlarr.
- **prowlarr_search_query_timeout** (default: 120): The timeout for Prowlarr search queries, in seconds.
- **prowlarr_search_interval_hour** (default: 24): How often Prowlarr searches are initiated, in hours.
- **prowlarr_immediate_max_process** (default: 10): Maximum number of immediate Prowlarr processes.
- **prowlarr_immediate_max_process_time** (default: 15): Maximum time for immediate Prowlarr processes, in seconds.
- **prowlarr_feed_scrape_interval** (default: 3): Interval for Prowlarr feed scraping, in hours.

## Premiumize Settings

OAuth settings for Premiumize integration.

- **premiumize_oauth_client_id**: The OAuth client ID for Premiumize.
- **premiumize_oauth_client_secret**: The OAuth client secret for Premiumize.

## Zilean Settings
- **zilean_url** (default: `"http://zilean-service:9696"`): The Zilean service URL.
- **zilean_search_interval_hour** (default: 24): How often Zilean searches are initiated, in hours.

## Configuration Sources

These settings define where MediaFusion looks for its configuration files.

- **remote_config_source** (default: GitHub RAW URL): The URL of the remote configuration source.
- **local_config_path** (default: `"resources/json/scraper_config.json"`): The path to the local configuration file.

## Feature Toggles

These boolean flags control various features of MediaFusion.

- **is_scrap_from_torrentio** (default: `False`): Enable or disable scraping from Torrentio.
- **is_scrap_from_zilean** (default: `False`): Enable or disable scraping from Zilean.
- **enable_rate_limit** (default: `True`): Enable or disable rate limiting.
- **validate_m3u8_urls_liveness** (default: `True`): Enable or disable the validation of M3U8 URLs for liveness.
- **disable_download_via_browser** (default: `False`): If set to `True`, disables downloads through the browser.

## Content Filtering

Settings related to content filtering and moderation.

- **adult_content_regex_keywords**: A regular expression pattern to identify adult content keywords.

## Time-related Settings

These settings control various time-based behaviors in the application.

- **torrentio_search_interval_days** (default: 3): How often Torrentio searches are initiated, in days.
- **meta_cache_ttl** (default: 1800): The time-to-live (TTL) for cached metadata, in seconds (30 minutes by default).
- **worker_max_tasks_per_child** (default: 20): The maximum number of tasks per dramatiq worker child process. This setting helps prevent memory leaks.

## Scheduler Settings

These settings control the various scheduled tasks in MediaFusion.

### Global Scheduler Setting
- **disable_all_scheduler** (default: `False`): If set to `True`, disables all scheduled tasks.

### Individual Scheduler Settings
Each scheduler has a crontab expression to define when it runs and a corresponding disable flag.

- **tamilmv_scheduler_crontab** (default: `"0 */3 * * *"`)
- **disable_tamilmv_scheduler** (default: `False`)
- **tamil_blasters_scheduler_crontab** (default: `"0 */6 * * *"`)
- **disable_tamil_blasters_scheduler** (default: `False`)
- **formula_tgx_scheduler_crontab** (default: `"*/30 * * * *"`)
- **disable_formula_tgx_scheduler** (default: `False`)
- **nowmetv_scheduler_crontab** (default: `"0 0 * * *"`)
- **disable_nowmetv_scheduler** (default: `False`)
- **nowsports_scheduler_crontab** (default: `"0 10 * * *"`)
- **disable_nowsports_scheduler** (default: `False`)
- **tamilultra_scheduler_crontab** (default: `"0 8 * * *"`)
- **disable_tamilultra_scheduler** (default: `False`)
- **validate_tv_streams_in_db_crontab** (default: `"0 */6 * * *"`)
- **disable_validate_tv_streams_in_db** (default: `False`)
- **sport_video_scheduler_crontab** (default: `"*/20 * * * *"`)
- **disable_sport_video_scheduler** (default: `False`)
- **streamed_scheduler_crontab** (default: `"*/30 * * * *"`)
- **disable_streamed_scheduler** (default: `False`)
- **streambtw_scheduler_crontab** (default: `"*/15 * * * *"`)
- **disable_streambtw_scheduler** (default: `False`)
- **dlhd_scheduler_crontab** (default: `"25 * * * *"`)
- **disable_dlhd_scheduler** (default: `False`)
- **update_imdb_data_crontab** (default: `"0 2 * * *"`)
- **motogp_tgx_scheduler_crontab** (default: `"0 5 * * *"`)
- **disable_motogp_tgx_scheduler** (default: `False`)
- **update_seeders_crontab** (default: `"0 0 * * *"`)
- **arab_torrents_scheduler_crontab** (default: `"0 0 * * *"`)
- **disable_arab_torrents_scheduler** (default: `False`)
- **wwe_tgx_scheduler_crontab** (default: `"10 */3 * * *"`)
- **disable_wwe_tgx_scheduler** (default: `False`)
- **ufc_tgx_scheduler_crontab** (default: `"30 */3 * * *"`)
- **disable_ufc_tgx_scheduler** (default: `False`)
- **prowlarr_feed_scraper_crontab** (default: `"0 */3 * * *"`)
- **disable_prowlarr_feed_scraper** (default: `False`)

Note: Crontab expressions follow the standard cron format: "minute hour day-of-month month day-of-week".


#### Scheduler Crontabs
> [!TIP]
> To setup the scheduler crontabs, you can use [crontab.guru](https://crontab.guru/) to generate the crontab expressions.
- **disable_all_scheduler** (default: `False`): Disable all schedulers.
- **tamilmv_scheduler_crontab** (default: `"0 */3 * * *"`): Scheduler for TamilMV.
- **disable_tamilmv_scheduler** (default: `False`): Disable TamilMV scheduler.
- **tamil_blasters_scheduler_crontab** (default: `"0 */6 * * *"`): Scheduler for Tamil Blasters.
- **disable_tamil_blasters_scheduler** (default: `False`): Disable Tamil Blasters scheduler.
- **tamilultra_scheduler_crontab** (default: `"0 8 * * *"`): Scheduler for TamilUltra.
- **disable_tamilultra_scheduler** (default: `False`): Disable TamilUltra scheduler.
- **formula_tgx_scheduler_crontab** (default: `"*/30 * * * *"`): Scheduler for Formula TGX.
- **disable_formula_tgx_scheduler** (default: `False`): Disable Formula TGX scheduler.
- **nowmetv_scheduler_crontab** (default: `"0 0 * * 5"`): Scheduler for NowMeTV.
- **disable_nowmetv_scheduler** (default: `False`): Disable NowMeTV scheduler.
- **nowsports_scheduler_crontab** (default: `"0 10 * * *"`): Scheduler for NowSports.
- **disable_nowsports_scheduler** (default: `False`): Disable NowSports scheduler.
- **streamed_scheduler_crontab** (default: `"*/15 * * * *"`): Scheduler for Streamed.su Sports Events.
- **disable_streamed_scheduler** (default: `False`): Disable Streamed.su scheduler.
- **sport_video_scheduler_crontab** (default: `"*/20 * * * *"`): Scheduler for Sport Video.
- **disable_sport_video_scheduler** (default: `False`): Disable Sport Video scheduler.
- **streambtw_scheduler_crontab** (default: `"*/15 * * * *"`): Scheduler for Streambtw.
- **disable_streambtw_scheduler** (default: `False`): Disable Streambtw scheduler.
- **dlhd_scheduler_crontab** (default: `"25 * * * *"`): Scheduler for DLHD.
- **disable_dlhd_scheduler**: Disable DLHD scheduler.
- **update_imdb_data_crontab** (default: `"0 2 * * *"`): Scheduler for updating IMDb data.
- **motogp_tgx_scheduler_crontab** (default: `"0 5 * * *"`): Scheduler for MotoGP TGX.
- **disable_motogp_tgx_scheduler**: Disable MotoGP TGX scheduler.

### How to Configure

#### Configuration for k8s
To configure these settings, locate the `env` section within your `deployment/local-deployment.yaml` file. Add or update the environment variables like this example:

```yaml
env:
  - name: MONGO_URI
    value: "your_mongo_uri"
  - name: DB_MAX_CONNECTIONS
    value: "100"
  # Add other configurations as needed
```

#### Configuration for Docker Compose
To configure these settings, locate the `.env` file in the root directory of your MediaFusion deployment. Add or update the environment variables like this example:

```env
MONGO_URI=your_mongo_uri
DB_MAX_CONNECTIONS=100
# Add other configurations as needed
```

Remember to replace placeholder values with actual configuration values suited to your environment and requirements. This customization allows you to tailor MediaFusion to your specific setup and preferences.