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
