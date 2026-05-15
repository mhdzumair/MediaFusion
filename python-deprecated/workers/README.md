# Workers

Python background worker processes. These are **active production code** — they populate the database that the Rust API reads from.

## Components

| Directory | Purpose |
|-----------|---------|
| `../scrapers/` | Live scrapers (Prowlarr, Jackett, Torrentio, Zilean, etc.) |
| `../mediafusion_scrapy/` | Scrapy spiders (BT4G, public indexers with browser automation) |
| `../streaming_providers/` | Provider clients used by streaming/playback workers |
| `../api/taskiq_worker.py` | Worker entry point — run with `taskiq worker api.taskiq_worker:broker_*` |
| `../api/scheduler.py` | APScheduler cron jobs |
| `../api/task_queue.py` | Task dispatch infrastructure (taskiq/Redis streams) |
| `../migrations/` | Alembic DB migrations |

## Running Workers

```bash
# Default queue
taskiq worker api.taskiq_worker:broker_default

# Scrapy queue
taskiq worker api.taskiq_worker:broker_scrapy

# Import queue
taskiq worker api.taskiq_worker:broker_import

# Priority queue
taskiq worker api.taskiq_worker:broker_priority
```

## What Stays in Python Permanently

- **APScheduler background workers** — taskiq/APScheduler with no Rust equivalent worth porting
- **Scrapy spiders** — browser automation + Cloudflare solver (Scrapling) for BT4G/public indexers
- **DLHD scraper** — background-only APScheduler cron (disabled by default)
- **Telegram admin operations** — Pyrogram MTProto admin (distinct from grammers live scraping)

## Architecture

```
                    ┌─────────────────────┐
                    │   services/api      │  ← Rust primary API (read-heavy)
                    │   (Rust, port 8001) │
                    └────────┬────────────┘
                             │ reads
                    ┌────────▼────────────┐
                    │    PostgreSQL DB     │
                    └────────▲────────────┘
                             │ writes
              ┌──────────────┴──────────────┐
              │         Python Workers       │
              │  scrapers/ + scrapy/ + ...   │
              └─────────────────────────────┘
```
