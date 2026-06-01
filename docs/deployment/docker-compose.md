# Docker Compose Deployment

The recommended way to self-host MediaFusion. Docker Compose manages all services — MediaFusion, PostgreSQL, Redis, and Prowlarr — in a single stack.

!!! tip "Video tutorial"
    A video walkthrough is available at [video.elfhosted.com/w/rgRFCmdgWW2HDES4QSD6Kb](https://video.elfhosted.com/w/rgRFCmdgWW2HDES4QSD6Kb).

## Prerequisites

Install these tools before starting:

- [Docker](https://docs.docker.com/get-docker/) (includes Docker Compose on modern installs)
- [mkcert](https://github.com/FiloSottile/mkcert#installation) — for self-signed HTTPS certificates

Verify your Docker installation:

```bash
docker --version
docker compose version
```

## Step 1: Clone the repository

```bash
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion/deployment/docker-compose
```

## Step 2: Configure environment variables

=== "Linux / macOS"

    ```bash
    cp .env-sample .env

    # Generate a secure secret key
    echo "SECRET_KEY=$(openssl rand -hex 16)" >> .env

    # Set the API password (protects admin endpoints)
    echo "API_PASSWORD=your_strong_password" >> .env

    # Set your contact email
    echo "CONTACT_EMAIL=you@example.com" >> .env

    # Set the public URL of your instance
    # Use https://mediafusion.local for local-only, or your real domain
    echo 'HOST_URL=https://mediafusion.local' >> .env
    ```

=== "Windows (PowerShell)"

    ```powershell
    Copy-Item .env-sample .env

    $secret = [System.Guid]::NewGuid().ToString("N").Substring(0, 32)
    Add-Content -Path .env -Value "SECRET_KEY=$secret"
    Add-Content -Path .env -Value "API_PASSWORD=your_strong_password"
    Add-Content -Path .env -Value "CONTACT_EMAIL=you@example.com"
    Add-Content -Path .env -Value 'HOST_URL=https://mediafusion.local'
    ```

!!! tip "Using a real domain?"
    Set `HOST_URL` to your actual domain (e.g. `https://mediafusion.yourdomain.com`) and configure a reverse proxy (nginx, Caddy, Traefik) in front of the stack. Skip the `mkcert` step below.

Review the full `.env` file and adjust anything else before proceeding. See [Configuration Overview](../configuration/index.md) for all options.

## Step 3: Generate a local SSL certificate

This is required for Stremio to accept connections from your local instance.

=== "Linux / macOS"

    ```bash
    mkcert -install
    mkcert "mediafusion.local"
    ```

=== "Windows (PowerShell)"

    ```powershell
    mkcert -install
    mkcert "mediafusion.local"
    ```

    !!! tip "WSL users"
        Also run `mkcert -install` in a regular Windows PowerShell (not WSL) to install the root certificate system-wide.

## Step 4: Configure Prowlarr (optional but recommended)

Prowlarr aggregates torrent indexers and feeds results to MediaFusion. The setup script configures it automatically:

=== "Linux / macOS"

    ```bash
    ./setup-prowlarr.sh
    ```

=== "Windows (PowerShell)"

    ```powershell
    .\setup-prowlarr.ps1
    ```

The script writes a `PROWLARR_API_KEY` to your `.env` and adds a set of working public trackers.

!!! note
    You can add more indexers at `http://localhost:9696` after the stack starts.

## Step 5: Start the stack

### Standard deployment (recommended for most users)

```bash
docker compose -f docker-compose.yml up -d
```

This starts a single PostgreSQL instance with one background worker. Suitable for personal or small shared use.

### High-availability deployment

For high read workloads (e.g. shared instance):

```bash
docker compose -f docker-compose.yml -f docker-compose-postgres-ha.yml up -d
```

This adds a PostgreSQL read replica, PgBouncer connection pooling, and dedicated workers per queue.

## Step 6: Add to your hosts file

Map `mediafusion.local` to `127.0.0.1` so your browser resolves the local domain:

=== "Linux / macOS"

    ```bash
    echo "127.0.0.1 mediafusion.local" | sudo tee -a /etc/hosts
    ```

=== "Windows (PowerShell — run as Administrator)"

    ```powershell
    Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "127.0.0.1 mediafusion.local"
    ```

Now open [https://mediafusion.local](https://mediafusion.local) in your browser.

!!! tip "First-time scraping delay"
    Results may be sparse for the first hour while background scrapers populate the database. Visit `https://mediafusion.local/scraper` to trigger scrapers manually.

---

## Compose file reference

| File | Use case |
|---|---|
| `docker-compose.yml` | Standard single-PostgreSQL deployment |
| `docker-compose-postgres-ha.yml` | Add-on: PostgreSQL HA with read replica + PgBouncer |
| `docker-compose-minimal.yml` | Databases only — for local development |

---

## Updating MediaFusion

```bash
git pull
docker compose -f docker-compose.yml down
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d
```

!!! warning "Before updating to a new major version"
    Check the [release notes](https://github.com/mhdzumair/MediaFusion/releases) for any migration steps. Major schema changes require the old version to be stopped cleanly before updating.

## Stopping and resetting

```bash
# Stop without removing data
docker compose -f docker-compose.yml down

# Stop and wipe all data (destructive!)
docker compose -f docker-compose.yml down -v
```

## Troubleshooting

**Check logs for a specific service:**
```bash
docker compose -f docker-compose.yml logs -f mediafusion
docker compose -f docker-compose.yml logs -f postgres
```

**Common issues:**

| Symptom | Likely cause |
|---|---|
| SSL certificate errors in browser | `mkcert -install` not run, or not run in Windows PowerShell for WSL |
| "Connection refused" | Stack not started, or `HOST_URL` mismatch |
| Empty catalogs | Scrapers haven't run yet — trigger manually at `/scraper` |
| Database connection errors | `POSTGRES_URI` not set correctly in `.env` |
