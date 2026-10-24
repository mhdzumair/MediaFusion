# Security

Configuration options for protecting your MediaFusion instance.

## API password

| Variable | Description |
|---|---|
| `API_PASSWORD` | **Required.** Protects admin endpoints and the API. Set a strong, unique password. |

The API password is required to:
- Access the scraper control interface (`/scraper`)
- Use admin endpoints
- Configure the instance programmatically

Users configure the addon through the web UI (`/configure`), which does not require the API password directly — it is baked into their encrypted manifest URL.

## Secret key

| Variable | Description |
|---|---|
| `SECRET_KEY` | **Required.** Used to encrypt user configuration data stored in manifest URLs. |

User data (provider credentials, filters) is encrypted with this key before being embedded in the manifest URL. Changing this key invalidates all existing user manifests — every user will need to reconfigure.

**Generate a strong key:**

=== "Linux / macOS"

    ```bash
    openssl rand -hex 16
    ```

=== "Windows (PowerShell)"

    ```powershell
    [System.Guid]::NewGuid().ToString("N").Substring(0, 32)
    ```

## Rate limiting

| Variable | Default | Description |
|---|---|---|
| `ENABLE_RATE_LIMIT` | `false` | Enable per-IP rate limiting |

Enable rate limiting on public instances to prevent abuse. Limits are enforced with a Redis token bucket.

## HTTPS

MediaFusion requires HTTPS for Stremio to accept stream URLs. Options:

- **Behind a reverse proxy** (nginx, Caddy, Traefik) — handle TLS termination at the proxy
- **Docker Compose local** — use `mkcert` (see [Docker Compose guide](../deployment/docker-compose.md))
- **ElfHosted / managed** — TLS is handled automatically

!!! danger "Never expose MediaFusion over plain HTTP in production"
    Stremio will reject non-HTTPS addon manifests. More importantly, your provider API keys and user data travel in manifest URLs — always use HTTPS.

## Metrics endpoint

| Variable | Default | Description |
|---|---|---|
| `ENABLE_METRICS_ENDPOINT` | `true` | Expose Prometheus `/metrics` endpoint |
| `METRICS_BEARER_TOKEN` | `None` | If set, `/metrics` requires `Authorization: Bearer <token>` |
| `ENABLE_REQUEST_METRICS` | `true` | Track per-route p50/p95/p99 latency in Redis |

Secure the `/metrics` endpoint with a bearer token on public instances.

## Profiler

| Variable | Default | Description |
|---|---|---|
| `ENABLE_PROFILER` | `false` | Enable `?_profile=1` flamegraph (requires `X-API-Key` header) |

!!! danger "Never enable the profiler in production"
    The profiler endpoint exposes internal timing data and can cause performance degradation.

## Hide torrent identifiers on public instances

Set `HIDE_TORRENT_INFO_HASHES=true` to remove info hashes, magnets, and torrent-file
payloads from non-admin JSON responses, including Stremio, catalog streams, stream
links, suggestions, personal streams, watch history, and import analysis. Hashes in
filenames, descriptions, and behavior hints are redacted too. Authenticated admins
retain access for administration; moderator responses are still protected.

Playback links use confidential, authenticated references bound to the profile and
provider. The server restores the hash and original file selection internally.
References remain valid until `SECRET_KEY` changes. If a provider's final download
URL contains a hash, MediaFusion relays that video (including byte ranges) instead
of exposing the URL in a redirect. This edge case uses the instance's bandwidth.
P2P-only playback and non-admin Torznab access are unavailable in this mode.

The web UI requests streams using a proxy-readable resource path:

```text
/api/v1/stream/movie/tt0063350.json?profile_id=2&profile_uuid=...&provider=realdebrid
/api/v1/stream/series/tt0944947:1:2.json?profile_id=2&profile_uuid=...&provider=realdebrid
```

`tmdb:`, `tvdb:`, `mal:`, `kitsu:` and `mf:` IDs are supported as well. The old
catalog stream route remains available, with the same privacy protection and
provider restrictions. Responses contain a top-level `streams` array. The `url`
field is the canonical playback link; the browser derives any MediaFlow wrapper
from that field after a reverse proxy has rewritten it. A proxy can return rows
containing only `name`, `description`, and `url`; these remain visible and playable
regardless of saved quality filters.

The flag defaults to `false`. Restart the API after changing it and purge existing
reverse-proxy caches when enabling it, so old hash-bearing responses are not served.
