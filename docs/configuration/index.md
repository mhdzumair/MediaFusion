# Configuration Overview

MediaFusion is configured entirely through environment variables. All deployment methods (Docker Compose, Kubernetes, binary) read the same variables.

## The 5 required variables

You must set these before MediaFusion will start:

| Variable | Example | Description |
|---|---|---|
| `HOST_URL` | `https://mediafusion.yourdomain.com` | Public URL of your instance — must be reachable by Stremio |
| `SECRET_KEY` | *(generate below)* | Random secret used for encryption |
| `API_PASSWORD` | `your_strong_password` | Password to access admin endpoints and protect the API |
| `CONTACT_EMAIL` | `you@example.com` | Shown in addon metadata |
| `POSTGRES_URI` | `postgresql://user:pass@host:5432/db` | PostgreSQL connection string |

**Generate a secret key:**

=== "Linux / macOS"

    ```bash
    openssl rand -hex 16
    ```

=== "Windows (PowerShell)"

    ```powershell
    [System.Guid]::NewGuid().ToString("N").Substring(0, 32)
    ```

## Where to put variables

=== "Docker Compose (.env file)"

    Copy `.env-sample` to `.env` in `deployment/docker-compose/` and fill in the values. Docker Compose reads this file automatically.

    ```bash
    cp deployment/docker-compose/.env-sample deployment/docker-compose/.env
    ```

=== "Kubernetes (secrets)"

    Use `kubectl create secret generic mediafusion-secrets --from-literal=KEY=value`. See the [Kubernetes guide](../deployment/kubernetes.md).

=== "Direct binary (shell / systemd)"

    Source a `.env` file before starting:

    ```bash
    export $(cat .env | xargs)
    ./mediafusion-api
    ```

    Or use `EnvironmentFile=` in a systemd unit.

## Configuration sections

| Section | What it covers |
|---|---|
| [Core Settings](core.md) | Addon name, URL, branding, logging |
| [Database & Redis](database.md) | PostgreSQL, Redis, connection pools |
| [Streaming Providers](streaming-providers.md) | Debrid services, P2P, provider limits |
| [Content Sources](content-sources.md) | Scrapers, RSS feeds, Prowlarr, Zilean |
| [MediaFlow Proxy](mediaflow.md) | Proxy config for debrid and live streams |
| [Security](security.md) | API password, encryption, rate limiting |
| [Stream Formatting](stream-formatting.md) | Custom stream title/description templates |

## Full reference

The complete list of all 291 environment variables with types and defaults is in the [Environment Variable Reference](../reference/env-reference.md).
