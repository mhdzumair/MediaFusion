# Features

MediaFusion is more than an addon — it's a full streaming platform. Here's what it can do.

---

## Content & Catalogs

### Rich multi-language catalogs

Browse movies, series, and live TV in 50+ languages. Specialized catalogs for regional content:

- **South Asian** — Tamil, Malayalam, Telugu, Hindi, Kannada
- **East Asian** — Japanese (anime via Nyaa/AnimeTosho/SubsPlease), Chinese, Korean
- **Arabic** — dedicated Arab Torrents scraper
- **European** — English, Russian (Rutor), and more

### Sports & live events

MediaFusion has dedicated scrapers for live and on-demand sports content:

| Sport | Sources |
|---|---|
| Formula Racing (F1, F2, MotoGP) | formula-ext, motogp-ext |
| Combat Sports (UFC, WWE) | ufc-ext, wwe-ext |
| Multi-sport (NFL, NBA, MLB, soccer, hockey, rugby, cricket) | sport-video, dlhd, nowsports |
| Live TV | NowMeTV, DLHD, TamilUltra, and configurable IPTV |

### Live TV & IPTV

- Import **M3U playlists** from any source
- **Xtream Codes** IPTV provider support
- **AceStream** channel playback (via MediaFlow Proxy)
- **Stalker portal** support

---

## Stream Sources

MediaFusion aggregates streams from multiple sources simultaneously:

| Source | Type | Notes |
|---|---|---|
| **Prowlarr** | Torrent indexer aggregator | Recommended; supports 500+ indexers |
| **Jackett** | Torrent indexer aggregator | Alternative to Prowlarr |
| **Torznab** | Torrent API | Direct Torznab endpoint support |
| **Public indexers** | Built-in scrapers | 1337x, The Pirate Bay, Rutor, YTS, EZTV, LimeTorrents, BT4G, Nyaa, AnimeTosho, and more |
| **Torrentio** | External aggregator | Optional; fetches cached results |
| **Zilean DMM** | Debrid cache index | Fast cached-torrent lookups |
| **DMM Hashlist** | GitHub-hosted hashlist | Incremental sync from DebridMediaManager |
| **MediaFusion peer** | Another instance | Aggregate results from a second instance |
| **RSS feeds** | Configurable | Custom parsing patterns and scheduling |
| **Telegram** | Channel scraping | Optional; requires Telegram API credentials |
| **YouTube** | Channel scraping | Optional; requires YouTube API key |

### On-demand & background search

- **Live search**: when you open a title, MediaFusion fans out to all enabled scrapers in parallel — results appear within seconds
- **Background search**: a scheduler continuously re-scrapes popular titles to pre-populate the stream cache

---

## Streaming Providers

MediaFusion routes playback through your configured provider:

| Provider | Description |
|---|---|
| **Direct P2P** | WebTorrent — plays directly in Stremio, no account needed |
| **Real-Debrid** | Premium debrid — instant cached-torrent HTTP links |
| **AllDebrid** | Premium debrid |
| **Premiumize** | Premium debrid + cloud storage |
| **Debrid-Link** | Premium debrid |
| **Torbox** | Free-tier debrid with premium upgrade |
| **EasyDebrid** | Debrid service |
| **OffCloud** | Cloud storage + debrid |
| **PikPak** | Free-quota cloud storage |
| **Seedr.cc** | Free-quota cloud storage |
| **qBittorrent WebDAV** | Self-hosted download → stream pipeline |
| **StremThru** | Multi-debrid interface |

You can configure up to 5 providers per profile. Each provider is checked for cached status before being shown.

---

## Stream Quality & Filtering

### Rich stream metadata

Torrent and usenet streams store parsed release metadata (resolution, codec, languages, HDR, audio) in the database. Existing rows ingested before v6 or without PTT at scrape time can be filled with the worker job `backfill_stream_metadata` — see [Worker CLI — backfill_stream_metadata](deployment/worker-cli.md#backfill_stream_metadata).

Every stream result shows:
- Resolution (480p → 4K)
- HDR format (SDR, HDR10, HDR10+, Dolby Vision)
- Audio format (AAC, AC3, DTS, Atmos, TrueHD)
- Audio channels (stereo, 5.1, 7.1)
- File size
- Seeder count (P2P)
- Cached status per debrid provider
- Upload date
- Source indexer

### Customizable filters

Per-user stream filters configurable from the web UI:
- Minimum/maximum resolution
- File size range
- Minimum seeders (P2P)
- Show only cached streams
- Filter by audio language
- Sort order: quality, size, seeders, cached-first, upload date

### Keyword filters & quality presets

Server-level keyword filter rules can block unwanted releases (e.g. `CAM`, `HDCAM`, specific uploaders).

---

## Client Support

### Stremio

Full Stremio addon — catalogs, metadata, stream links, watchlist sync.

### Kodi

Native Kodi video addon with the same catalog/stream experience. Install via the MediaFusion repository for automatic updates.

### In-browser player

MediaFusion includes a built-in web player — stream directly in your browser without installing Stremio.

### Torznab indexer

MediaFusion exposes a Torznab-compatible API endpoint usable as an indexer in:
- **Radarr** (movie auto-management)
- **Sonarr** (TV series auto-management)
- **Prowlarr** (as a meta-indexer source)

---

## User Features

### Watchlist sync

Sync your debrid provider's watchlist as a Stremio catalog. Supported integrations:
- Trakt (OAuth sync)
- Simkl (OAuth sync)
- Real-Debrid watchlist
- Manual watch history

### User profiles & multi-provider

Each user gets their own encrypted profile. A profile can contain:
- Up to 5 streaming providers
- Custom stream filters
- Personal API keys (TMDB, RPDB, etc.)
- Custom catalog preferences

### RPDB poster ratings

Overlay IMDb ratings directly on poster images using your [RPDB](https://ratingposterdb.com/) API key.

### Parental controls

Filter content by:
- Nudity rating
- Age certification (PG, PG-13, R, etc.)

### Manual torrent import

- Paste a magnet link or upload a `.torrent` file directly from the web UI
- Private torrent support
- Webseed (HTTP-seeded) torrent support
- [Browser extension](integrations/browser-extension.md) for one-click import from torrent sites

---

## Admin & Operations

### Scraper control panel

Admin UI at `/scraper` to:
- Trigger any scraper manually
- View scraper schedules and last-run times
- Monitor job queue status

### Prometheus metrics

Expose a `/api/v1/metrics` endpoint for Grafana dashboards:
- Per-route request latency (p50/p95/p99)
- Database pool stats
- Cache hit rates
- Background job counters

### Exception tracking

Built-in Redis-backed exception tracker — records the last N distinct errors with TTL, accessible from the admin panel.

### RSS feed manager

Full RSS feed management system:
- Add any RSS feed URL
- Custom title parsing patterns (regex)
- Per-feed catalog type detection
- Scheduled auto-refresh
- Filter rules

### Annotation queue

Users can flag streams for quality annotation (wrong metadata, bad quality). Operators review and approve/reject via the admin panel.

---

## Security

- **Encrypted user data** — all user config (API keys, provider credentials) is AES-256 encrypted and embedded in the manifest URL. The server never stores plaintext credentials.
- **API password** — optional password protection for admin endpoints
- **Rate limiting** — per-IP Redis token bucket (configurable)
- **DMCA compliance** — torrent block list for DMCA take-down requests
