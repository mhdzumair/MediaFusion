# What is MediaFusion?

MediaFusion is an **open-source streaming platform** that aggregates stream sources, manages playback through your chosen providers, and delivers content to Stremio, Kodi, or your browser.

It is more than a simple addon — it runs as a full server that handles catalog browsing, stream discovery, debrid resolution, live TV, and user profile management.

## How it works

```
Stremio / Kodi / Browser
        │  (Stremio addon protocol / web)
        ▼
MediaFusion server          ← this is what you install / self-host
        │
        ├── Stream discovery (Prowlarr, public indexers, Torrentio, Zilean, RSS…)
        ├── Debrid resolution (Real-Debrid, AllDebrid, Torbox…)
        ├── Catalog management (movies, series, live TV, sports)
        ├── User profile encryption (AES-256 in manifest URL)
        │
        ├── PostgreSQL    ← metadata, stream index
        └── Redis         ← cache, task queue, rate limits
```

## Key concepts

**Catalogs** — MediaFusion exposes searchable, filterable catalogs of movies, series, live TV channels, and sports events. Each item links to discovered streams.

**Stream sources** — streams come from multiple places, queried in parallel:
- Torrent indexers (Prowlarr, Jackett, 1337x, TPB, YTS, Nyaa, and more)
- Debrid caches (Zilean DMM, Torrentio)
- IPTV / M3U playlists
- AceStream channels
- Telegram, YouTube, RSS feeds

**Streaming providers** — when you play something, MediaFusion routes it through your configured provider. A debrid provider downloads the torrent to a fast server and returns a direct HTTP link. P2P plays directly.

**Encrypted user profiles** — all user config (provider credentials, API keys, filters) is AES-256 encrypted by the server and embedded in the manifest URL. The server stores no plaintext credentials.

**Self-hosting vs community instances** — community instances ([mediafusion.elfhosted.com](https://mediafusion.elfhosted.com), [mediafusionfortheweebs.midnightignite.me](https://mediafusionfortheweebs.midnightignite.me)) are free to use. Running your own instance gives you custom scrapers, private API keys, and full control.

## Next steps

- [Quick Start](quick-start.md) — connect to a community instance in 2 minutes
- [Features](../features.md) — full overview of what MediaFusion can do
- [Choosing a Deployment](choosing-deployment.md) — decide whether to self-host and which method fits you
