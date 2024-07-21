### MediaFusion Environment Configuration Guide

This guide describes the environment variables available in MediaFusion for configuration. These settings control various aspects of the application, including database connections, service URLs, logging levels, feature toggles, and more. You can set these variables to customize MediaFusion according to your requirements.

#### Database Configuration

- **mongo_uri** (required): The MongoDB URI connection string.
- **db_max_connections** (default: 50): The maximum number of connections to the database.

#### Service URLs

- **redis_url** (default: `"redis://redis-service:6379"`): The Redis service URL.
- **prowlarr_url** (default: `"http://prowlarr-service:9696"`): The Prowlarr service URL.
- **torrentio_url** (default: `"https://torrentio.strem.fun"`): The Torrentio / KightCrawler URL.

#### Application Settings

- **addon_name** (default: `"MediaFusion"`): The name of the MediaFusion addon. You can customize this value identify the addon.
- **logo_url** (default: GitHub RAW Addon URL): The URL of the MediaFusion logo.
- **secret_key** (required): A secret key for securely signing the session.
- **host_url** (required): The URL where MediaFusion is hosted.
- **poster_host_url** (default: Use the Host URL value): The URL where MediaFusion is hosted. Use the same value as `host_url`. This setting intends to serve the poster images from the cached location.
- **logging_level** (default: `"INFO"`): The logging level of the application.
- **enable_tamilmv_search_scraper** (default: `False`): Toggle the TamilMV search scraper.
- **is_scrap_from_torrentio** (default: `False`): Enable or disable scraping from Torrentio.
- **enable_rate_limit** (default: `True`): Enable or disable rate limiting.
- **meta_cache_ttl** (default: `1800`): The time-to-live (TTL) for cached metadata, in seconds.
- **validate_m3u8_urls_liveness** (default: `True`): Enable or disable the validation of M3U8 URLs for liveness. If enabled, the URLs are checked for liveness before returning them.
- **worker_max_tasks_per_child** (default: `20`): The maximum number of tasks per dramatiq worker child process. This setting helps prevent memory leaks.

#### Security and Authentication

- **premiumize_oauth_client_id** and **premiumize_oauth_client_secret**: OAuth credentials for Premiumize, if used.
- **prowlarr_api_key**: The API key for Prowlarr, if used.
- **api_password**: The password for accessing the API, if authentication is enabled.
- **is_public_instance** (default: `False`): Set to `True` for community instances that do not require authentication to access the data but protecting the `/scraper` endpoint and uploading live tv data endpoints.

#### Scraper and Scheduler Settings

- **scraper_proxy_url**: The proxy URL for the scraper, if any.
- **prowlarr_search_interval_hour** (default: `24`): How often Prowlarr searches are initiated, in hours.
- **prowlarr_immediate_max_process** (default: `10`) and **prowlarr_immediate_max_process_time** (default: 15): Settings related to the immediate processing of Prowlarr searches.
- **torrentio_search_interval_days** (default: `3`): How often Torrentio searches are initiated, in days.
- **prowlarr_live_title_search** (default: `False`): Enable or disable live title search in Prowlarr. If False, search movie/series by title in background worker So that you won't get the result at first.
- **prowlarr_background_title_search**: Enable or disable background title search in Prowlarr.
- **prowlarr_search_query_timeout** (default: `120`): The timeout for Prowlarr search queries, in seconds.

#### Content Filters

- **adult_content_regex_keywords** (default: `r"(^|\b|\s)(18\+|adult|porn|sex|xxx|nude|naked|erotic|sexy|18\s*plus)(\b|\s|$|[._-])"`): The regular expression for adult content keywords.

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
- **mrgamingstreams_scheduler_crontab** (default: `"*/15 * * * *"`): Scheduler for MrGamingStreams Sports Events.
- **disable_mrgamingstreams_scheduler** (default: `True`): Disable MrGamingStreams scheduler.
- **sport_video_scheduler_crontab** (default: `"*/20 * * * *"`): Scheduler for Sport Video.
- **disable_sport_video_scheduler** (default: `False`): Disable Sport Video scheduler.
- **crictime_scheduler_crontab** (default: `"*/15 * * * *"`): Scheduler for Crictime.
- **disable_crictime_scheduler** (default: `False`): Disable Crictime scheduler.
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