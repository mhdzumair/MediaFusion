# Core Settings

General application settings that control how MediaFusion presents itself and behaves.

## Application identity

| Variable | Default | Description |
|---|---|---|
| `ADDON_NAME` | `MediaFusion` | Name shown in Stremio and Kodi addon listings |
| `DESCRIPTION` | *(built-in)* | Addon description shown in metadata |
| `HOST_URL` | **required** | Public URL of your instance |
| `CONTACT_EMAIL` | **required** | Shown in addon metadata |
| `LOGO_URL` | *(MediaFusion logo)* | Custom logo URL |
| `BRANDING_SVG` | `None` | Optional partner/host SVG logo URL |
| `VERSION` | `1.0.0` | Version string shown in metadata |

## UI appearance

| Variable | Default | Options |
|---|---|---|
| `DEFAULT_COLOR_SCHEME` | `mediafusion` | `mediafusion`, `cinematic`, `ocean`, `forest`, `emeraldnight`, `midnight`, `arctic`, `slate`, `rose`, `purple`, `sunset`, `youtube` |

## Poster settings

| Variable | Default | Description |
|---|---|---|
| `POSTER_HOST_URL` | `None` | URL for poster images; falls back to `HOST_URL` if not set |

## Logging

| Variable | Default | Options |
|---|---|---|
| `LOGGING_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Set `DEBUG` only during development — it produces very verbose output and slows down the server.

## Metadata sources

| Variable | Default | Description |
|---|---|---|
| `METADATA_PRIMARY_SOURCE` | `imdb` | `imdb` or `tmdb` — which metadata source to prefer |
| `IMDB_CINEMETA_FALLBACK_ENABLED` | `true` | Fall back to Cinemeta when IMDb lookup fails |
| `TMDB_API_KEY` | `None` | Required when `METADATA_PRIMARY_SOURCE=tmdb` |
| `TVDB_API_KEY` | `None` | Optional: enables TVDB metadata |

## Discover feature

| Variable | Default | Description |
|---|---|---|
| `DISCOVER_ENABLED` | `true` | Enable the Discover catalog section |
| `DISCOVER_ALLOW_SERVER_KEY` | `false` | Use the server's TMDB key for users without their own |

## Content type controls

Globally disable specific content types. Affects imports, stream delivery, and UI visibility.

| Variable | Default | Description |
|---|---|---|
| `DISABLED_CONTENT_TYPES` | `[]` | List of types to disable: `magnet`, `torrent`, `nzb`, `iptv`, `youtube`, `http`, `acestream`, `telegram` |

**Example** — disable IPTV (hides M3U and Xtream tabs entirely):

```bash
DISABLED_CONTENT_TYPES='["iptv"]'
```

## Public instance settings

| Variable | Default | Description |
|---|---|---|
| `IS_PUBLIC_INSTANCE` | `false` | Enable public instance mode — disables some admin features and enables public signup |

## Scraping size threshold

| Variable | Default | Description |
|---|---|---|
| `MIN_SCRAPING_VIDEO_SIZE` | `26214400` | Minimum file size in bytes (25 MB) to consider a torrent file a valid video |
