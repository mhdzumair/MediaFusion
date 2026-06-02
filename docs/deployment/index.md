# Deployment Overview

MediaFusion supports four self-hosted deployment methods plus a fully managed option.

!!! warning "Always pin a version tag in production"
    Never use `latest` or `beta` in production. Pin to a specific release tag such as `mhdzumair/mediafusion:6.0.0`.

    - `latest` — tracks the most recent stable release
    - `beta` — tracks the most recent beta release

## Deployment methods

| Method | Guide | Best for |
|---|---|---|
| ElfHosted | [ElfHosted](elfhosted.md) | Managed private instance, zero ops |
| Docker Compose | [Docker Compose](docker-compose.md) | Home servers, VPS, beginners |
| Direct Binary | [Binary](binary.md) | Minimal footprint, no container runtime |
| Kubernetes | [Kubernetes](kubernetes.md) | Production, horizontal scaling |
| Local Development | [Local Dev](local-dev.md) | Contributing to MediaFusion |

## Services overview

Every MediaFusion deployment consists of these components:

| Component | Role |
|---|---|
| **mediafusion-api** | Main HTTP server — handles Stremio/Kodi requests |
| **mediafusion-worker** | Background worker — scrapers, RSS feeds, imports |
| **PostgreSQL** | Primary database — metadata, stream index |
| **Redis** | Cache, task queue, rate limiting |
| **Prowlarr** *(optional)* | Torrent indexer aggregator |
| **Browserless** *(optional)* | Headless Chrome for scraping Cloudflare-protected sites |

## Database migrations

Migrations run **automatically** at startup — no separate migration step needed.

For details on checking migration status or rolling back, see [Database Migrations](migrations.md).

## One-shot worker jobs

Manual scrapes, imports, and maintenance (including PTT metadata backfill on existing streams) are run via the worker CLI — see [Worker CLI Reference](worker-cli.md).
