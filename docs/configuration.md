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

- **secret_key** (required): A secret key for securely signing the session.
- **host_url** (default: `"https://mediafusion.fun"`): The URL where MediaFusion is hosted.
- **logging_level** (default: `"INFO"`): The logging level of the application.
- **enable_tamilmv_search_scraper** (default: `False`): Toggle the TamilMV search scraper.
- **is_scrap_from_torrentio** (default: `False`): Enable or disable scraping from Torrentio.
- **enable_rate_limit** (default: `True`): Enable or disable rate limiting.

#### Security and Authentication

- **premiumize_oauth_client_id** and **premiumize_oauth_client_secret**: OAuth credentials for Premiumize, if used.
- **prowlarr_api_key**: The API key for Prowlarr, if used.
- **api_password**: The password for accessing the API, if authentication is enabled.

#### Scraper and Scheduler Settings

- **scraper_proxy_url**: The proxy URL for the scraper, if any.
- **prowlarr_search_interval_hour** (default: `24`): How often Prowlarr searches are initiated, in hours.
- **prowlarr_immediate_max_process** (default: `10`) and **prowlarr_immediate_max_process_time** (default: 15): Settings related to the immediate processing of Prowlarr searches.
- **torrentio_search_interval_days** (default: `3`): How often Torrentio searches are initiated, in days.

#### Content Filters

- **adult_content_regex_keywords** (default: `r"(^|\b|\s)(18\+|adult|porn|sex|xxx|nude|naked|erotic|sexy|18\s*plus)(\b|\s|$|[._-])"`): The regular expression for adult content keywords.

#### Scheduler Crontabs
> [!TIP]
> To setup the scheduler crontabs, you can use [crontab.guru](https://crontab.guru/) to generate the crontab expressions.

- **tamilmv_scheduler_crontab** (default: `"0 */3 * * *"`): Scheduler for TamilMV.
- **tamil_blasters_scheduler_crontab** (default: `"0 */6 * * *"`): Scheduler for Tamil Blasters.
- **formula_tgx_scheduler_crontab** (default: `"0 */12 * * *"`): Scheduler for Formula TGX.
- **mhdtvworld_scheduler_crontab** (default: `"0 0 * * 5"`): Scheduler for MHDTVWorld.
- **mhdtvsports_scheduler_crontab** (default: `"0 10 * * *"`): Scheduler for MHDTVSports.

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