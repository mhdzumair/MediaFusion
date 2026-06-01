# Architecture

An overview of how MediaFusion is structured internally.

## System diagram

```
Stremio / Kodi clients
        │
        │  HTTPS  (Stremio addon protocol)
        ▼
┌─────────────────────────────────────┐
│         MediaFusion API server      │
│   (Rust / Axum + Gunicorn wrapper)  │
│                                     │
│  Middleware stack:                  │
│  ├─ RequestId                       │
│  ├─ TransientDatabaseRetry          │
│  ├─ SecureLogging                   │
│  ├─ Timing / Metrics                │
│  ├─ UserData (decrypt manifest)     │
│  ├─ APIKey validation               │
│  └─ RateLimit (token bucket)        │
│                                     │
│  Route handlers:                    │
│  ├─ /stream/...   ← most expensive  │
│  ├─ /playback/... ← debrid calls    │
│  ├─ /meta/...                       │
│  ├─ /catalog/...                    │
│  ├─ /manifest.json                  │
│  └─ /poster/...   ← Pillow/CPU      │
└────────────┬────────────────────────┘
             │
     ┌───────┴────────┐
     │                │
┌────▼────┐    ┌──────▼─────┐
│PostgreSQL│    │   Redis    │
│(primary +│    │(stream cache│
│ replica) │    │ task queue │
│+ PgBouncer│   │ rate limits)│
└──────────┘    └────────────┘
                      │
              ┌───────▼───────┐
              │Taskiq workers │
              │(background)   │
              │               │
              │ queues:       │
              │ • default     │
              │ • scrapy      │
              │ • import      │
              │ • priority    │
              └───────────────┘
```

## Route performance characteristics

| Route | Cost | Notes |
|---|---|---|
| `/stream/...` | High | DB + Redis MGET + optional live-scraper fan-out to N indexers |
| `/playback/...` | High | Debrid API calls, Redis lock, fire-and-forget tracking |
| `/meta/...` | Medium | Redis cache, occasional DB selectinload |
| `/catalog/...` | Medium | Redis cache, occasional DB |
| `/manifest.json` | Low | HMAC validation + Redis GET |
| `/poster/...` | CPU | Pillow image processing in thread pool |

## Background worker queues

| Queue | Purpose |
|---|---|
| `default` | General background tasks |
| `scrapy` | Scrapy-based web scraping |
| `import` | Torrent/magnet imports |
| `priority` | High-priority tasks (e.g. live events) |

In `TASKIQ_SINGLE_WORKER_MODE=true` (the default), all queues route to one worker process. Set to `false` in HA deployments to run dedicated processes per queue.

## Data flow: stream request

```
1. Stremio sends GET /stream/movie/tt1234567.json
2. API server decrypts user config from URL (SECRET_KEY)
3. Check Redis stream cache
   ├── Cache hit → return cached streams
   └── Cache miss →
       ├── Query PostgreSQL for indexed streams
       ├── If LIVE_SEARCH_STREAMS=true:
       │     Fan out to Prowlarr / Zilean / Torrentio / etc. in parallel
       │     Merge new results into DB
       └── Apply user filters (resolution, size, provider)
4. For each debrid provider:
   ├── Check debrid API for cached status
   └── Return cached stream URL or skip
5. Return stream list to Stremio
```

## Technology stack

| Component | Technology |
|---|---|
| API server | Rust (Axum), compiled to musl binary |
| Background worker | Rust (Taskiq) |
| Database ORM | SQLAlchemy async (Python), sqlx (Rust) |
| Migrations | sqlx |
| Cache / queue | Redis |
| Metadata scraping | Scrapy, Scrapling (Playwright) |
| Image generation | Pillow |
| Configuration | Pydantic Settings |

## Database schema

See the [database ERD](https://github.com/mhdzumair/MediaFusion/blob/main/docs/database-erd.md) for the full schema diagram.
