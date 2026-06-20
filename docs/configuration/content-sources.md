# Content Sources

Configure where MediaFusion fetches stream data. These are the scrapers, indexers, and feed integrations that populate your catalogs.

## Built-in stream sources

| Variable | Default | Description |
|---|---|---|
| `IS_SCRAP_FROM_TORRENTIO` | `true` | Fetch streams from Torrentio |
| `IS_SCRAP_FROM_MEDIAFUSION` | `true` | Fetch from the community MediaFusion index |
| `IS_SCRAP_FROM_ZILEAN` | `true` | Fetch cached content from [Zilean DMM](https://github.com/iPromKnight/zilean) |

## Prowlarr integration

Prowlarr aggregates torrent indexers and is the recommended way to get comprehensive torrent results.

| Variable | Default | Description |
|---|---|---|
| `PROWLARR_URL` | `http://prowlarr:9696` | Prowlarr base URL |
| `PROWLARR_API_KEY` | `None` | Prowlarr API key (generate in Prowlarr → Settings → General) |
| `PROWLARR_IMMEDIATE_MAX_PROCESS` | `30` | Max simultaneous Prowlarr searches on live requests |
| `PROWLARR_IMMEDIATE_MAX_PROCESS_TIME` | `30` | Timeout in seconds for live Prowlarr searches |
| `PROWLARR_SEARCH_INTERVAL_HOUR` | `24` | How often to re-search Prowlarr for cached titles (hours) |
| `PROWLARR_LIVE_TITLE_SEARCH` | `true` | Search Prowlarr in real time when a stream is requested |

See [Prowlarr Integration](../integrations/prowlarr.md) for setup instructions.

## Torznab endpoints

Add custom Torznab-compatible indexers beyond Prowlarr:

```bash
IS_SCRAP_FROM_TORZNAB=true
TORZNAB_ENDPOINTS='[
  {
    "name": "My Indexer",
    "url": "https://indexer.example/api?apikey=xxx",
    "enabled": true,
    "priority": 1,
    "categories": [2000, 5000]
  }
]'
```

## Background scrapers

These scrapers run on a schedule to keep catalogs fresh. Enable the ones relevant to your use case:

| Variable | Default | Description |
|---|---|---|
| `IS_SCRAP_FROM_ACESTREAM_BACKGROUND` | `true` | Scrape AceStream channels in the background |
| `ACESTREAM_BACKGROUND_SEARCH_API_KEY` | `None` | API key for AceStream search |
| `IS_SCRAP_FROM_YOUTUBE_BACKGROUND` | `false` | Scrape YouTube content |
| `YOUTUBE_API_KEY` | `None` | Required when YouTube scraping is enabled |
| `IS_SCRAP_FROM_TELEGRAM_BACKGROUND` | `false` | Scrape Telegram channels |

## Live search

| Variable | Default | Description |
|---|---|---|
| `LIVE_SEARCH_STREAMS` | `true` | Fan out to N scrapers in parallel when a stream is requested. Adds 1–5 s latency but surfaces fresher results. |

Disable this to reduce latency at the cost of fewer real-time results.

## Scrapling (browser-based scraping)

Used for public indexers that require JavaScript or Cloudflare bypass:

| Variable | Default | Description |
|---|---|---|
| `SCRAPLING_CDP_URL` | `None` | WebSocket URL of a running Browserless / Chrome DevTools endpoint (e.g. `ws://browserless:3000`) |
| `SCRAPLING_FETCHER_MODE` | `stealthy` | `stealthy` or `dynamic` |
| `SCRAPLING_HEADLESS` | `true` | Run browser headlessly |
| `SCRAPLING_SOLVE_CLOUDFLARE` | `true` | Attempt Cloudflare bypass |
| `SCRAPLING_REAL_CHROME` | `false` | Use a real Chrome binary instead of Playwright |
| `SCRAPLING_PROXY_URL` | `None` | Proxy for scrapling requests |
| `PUBLIC_INDEXERS_LIVE_SEARCH_ENABLE_CLOUDFLARE_SOLVER` | `false` | Enable Cloudflare solver for live searches |

!!! tip "Cloudflare solver"
    Leave `PUBLIC_INDEXERS_LIVE_SEARCH_ENABLE_CLOUDFLARE_SOLVER=false` unless you need it — it increases resource usage significantly. Requires a `SCRAPLING_CDP_URL` endpoint.

## Proxy settings

| Variable | Default | Description |
|---|---|---|
| `REQUESTS_PROXY_URL` | `None` | Route all outbound HTTP (scrapers + debrid API calls) through this proxy |
| `REQUESTS_PROXY_EXCLUDE_DEBRID_PROVIDERS` | `[]` | Comma-separated (or JSON array) debrid provider IDs that bypass the proxy and connect directly. E.g. `realdebrid,torbox`. Valid IDs: `realdebrid`, `seedr`, `debridlink`, `alldebrid`, `offcloud`, `pikpak`, `torbox`, `premiumize`, `stremthru`, `easydebrid`, `debrider`. |
| `SCRAPLING_PROXY_URL` | `None` | Separate proxy for browser-based scraping |
| `TCP_KEEPALIVE_SECS` | `15` | TCP keepalive interval for all outbound HTTP clients (seconds). Keeps the proxy tunnel's NAT/conntrack mappings alive during idle periods. |
| `EGRESS_WATCHDOG_ENABLED` | `true` | Restart the pod when sustained egress loss is detected (see [env reference](../reference/env-reference.md#http-client--egress)). |

## Scheduler control

| Variable | Default | Description |
|---|---|---|
| `DISABLE_ALL_SCHEDULER` | `false` | Disable all background scheduling (useful during development) |
| `TASKIQ_SINGLE_WORKER_MODE` | `true` | Route all task queues to one worker |
