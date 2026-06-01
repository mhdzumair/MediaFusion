# Architecture

An overview of how MediaFusion is structured internally.

## System diagram

```
Stremio / Kodi / Browser
        │
        │  HTTPS  (Stremio addon protocol / web)
        ▼
┌──────────────────────────────────────┐
│        MediaFusion API server        │
│         (Rust / Axum binary)         │
│                                      │
│  Middleware stack:                   │
│  ├─ RequestId                        │
│  ├─ TransientDatabaseRetry           │
│  ├─ SecureLogging                    │
│  ├─ Timing / Metrics                 │
│  ├─ UserData (decrypt manifest)      │
│  ├─ APIKey validation                │
│  └─ RateLimit (token bucket)         │
│                                      │
│  Route handlers:                     │
│  ├─ /stream/...   ← most expensive   │
│  ├─ /playback/... ← debrid calls     │
│  ├─ /meta/...                        │
│  ├─ /catalog/...                     │
│  ├─ /manifest.json                   │
│  ├─ /poster/...   ← image/imageproc  │
│  └─ /app          ← React SPA        │
└─────────────┬────────────────────────┘
              │
      ┌───────┴────────┐
      │                │
┌─────▼────┐    ┌──────▼──────┐
│PostgreSQL │    │    Redis    │
│(primary + │    │(stream cache│
│ replica)  │    │ task queue  │
│+ PgBouncer│    │ rate limits)│
└───────────┘    └──────┬──────┘
                        │
               ┌────────▼────────┐
               │  Background     │
               │  worker binary  │
               │  (Rust / Axum)  │
               │                 │
               │  Job handlers:  │
               │  • spiders      │
               │  • feed scrapers│
               │  • bg search    │
               │  • imports      │
               │  • maintenance  │
               └─────────────────┘
```

## Component overview

| Binary | Role | Source |
|---|---|---|
| `mediafusion-api` | Main HTTP server — Stremio/Kodi requests, web UI | `backend/src/bin/` |
| `mediafusion-worker` | Background jobs — scrapers, feeds, imports, maintenance | `backend/src/bin/worker.rs` |

Both binaries share the same codebase (`backend/`), compiled as two separate static musl binaries.

## Route performance characteristics

| Route | Cost | Notes |
|---|---|---|
| `/stream/...` | High | DB + Redis MGET + optional live-scraper fan-out to N indexers |
| `/playback/...` | High | Debrid API calls, Redis lock, fire-and-forget tracking |
| `/meta/...` | Medium | Redis cache, occasional DB query |
| `/catalog/...` | Medium | Redis cache, occasional DB query |
| `/manifest.json` | Low | HMAC validation + Redis GET |
| `/poster/...` | CPU | Image compositing via `image` + `imageproc` crates |

## Background worker jobs

The worker binary runs a `JobRegistry` — a scheduler that fires registered job handlers on configured crontabs.

| Job | Handler | Trigger |
|---|---|---|
| Public indexer spiders | `RegistryCrawl` | Per-scraper crontab |
| External scraper spiders | `FormulaExtCrawl`, `UfcExtCrawl`, `SportVideoCrawl`, etc. | Per-scraper crontab |
| Tamil forum scrapers | `TamilMvCrawl`, `TamilBlastersCrawl` | Crontab |
| Prowlarr feed | `ProwlarrFeedScraper` | `PROWLARR_FEED_SCRAPER_CRONTAB` |
| Jackett feed | `JackettFeedScraper` | `JACKETT_FEED_SCRAPER_CRONTAB` |
| RSS feeds | `RssFeedScraper` | `RSS_FEED_SCRAPER_CRONTAB` |
| DMM hashlist sync | `DmmHashlistScraper` | `DMM_HASHLIST_SCRAPER_CRONTAB` |
| Background search | `BackgroundSearch` | `BACKGROUND_SEARCH_CRONTAB` |
| YouTube scraper | `YoutubeBgScraper` | `YOUTUBE_BACKGROUND_SCRAPER_CRONTAB` |
| AceStream scraper | `AcestreamBgScraper` | `ACESTREAM_BACKGROUND_SCRAPER_CRONTAB` |
| Telegram scraper | `TelegramBgScraper` | `TELEGRAM_BACKGROUND_SCRAPER_CRONTAB` |
| M3U import | `M3uImport` | On demand |
| Xtream import | `XtreamImport` | On demand |
| IMDB dataset import | `ImdbDatasetImport` | On demand |
| Integration sync | `IntegrationSyncs` | `INTEGRATION_SYNC_CRONTAB` |
| Update seeders | `UpdateSeeders` | `UPDATE_SEEDERS_CRONTAB` |
| Validate TV streams | `ValidateTvStreams` | `VALIDATE_TV_STREAMS_IN_DB_CRONTAB` |
| Cleanup | `Cleanup` | `CLEANUP_EXPIRED_SCRAPER_TASK_CRONTAB` |

## Data flow: stream request

```
1. Stremio sends GET /stream/movie/tt1234567.json
2. API server decrypts user config from URL (AES-256, SECRET_KEY)
3. Check Redis stream cache
   ├── Cache hit → return cached streams immediately
   └── Cache miss →
       ├── Query PostgreSQL for indexed streams
       ├── If live search enabled:
       │     Acquire Redis SETNX lock (prevents thundering herd)
       │     Fan out to Prowlarr / Zilean / Torrentio / public indexers in parallel
       │     Merge new results into DB + cache
       └── Apply user filters (resolution, size, provider)
4. For each debrid provider in user profile:
   ├── Check debrid API for cached torrent status
   └── Return HTTP stream URL or skip
5. Return ranked stream list to Stremio
```

## Technology stack

| Component | Technology |
|---|---|
| API server | Rust (Axum), static musl binary |
| Background worker | Rust (custom `JobRegistry` scheduler) |
| Database | PostgreSQL + sqlx (async, compile-time checked queries) |
| Migrations | sqlx (`backend/migrations/`) |
| Cache / task queue | Redis |
| Image generation | Rust (`image` + `imageproc` crates) |
| Frontend | React (TypeScript), served at `/app` |
| Kodi addon | Python (`clients/kodi/`) |
| Browser extension | JavaScript (`clients/browser-extension/`) |
| Memory allocator | jemalloc (via `tikv-jemallocator`) |

## Database schema

See the [Database Schema](database-erd.md) page for the full ERD diagrams.
