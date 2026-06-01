# Direct Binary Deployment

Run MediaFusion with zero container overhead. The API server and background worker are distributed as **statically compiled musl binaries** — no Docker, no runtime dependencies. You only need PostgreSQL and Redis.

## Prerequisites

- **PostgreSQL 14+** — primary database
- **Redis 6+** — cache and task queue
- A publicly accessible URL for your instance

## Step 1: Download the binaries

Binaries are published as GitHub Release assets. Find the latest release at [github.com/mhdzumair/MediaFusion/releases](https://github.com/mhdzumair/MediaFusion/releases).

=== "Linux amd64"

    ```bash
    RELEASE=6.0.0   # replace with the latest release tag
    ARCH=amd64

    curl -Lo mediafusion-api \
      "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-api-linux-${ARCH}"

    curl -Lo mediafusion-worker \
      "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-worker-linux-${ARCH}"

    chmod +x mediafusion-api mediafusion-worker
    ```

=== "Linux arm64"

    ```bash
    RELEASE=6.0.0   # replace with the latest release tag
    ARCH=arm64

    curl -Lo mediafusion-api \
      "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-api-linux-${ARCH}"

    curl -Lo mediafusion-worker \
      "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-worker-linux-${ARCH}"

    chmod +x mediafusion-api mediafusion-worker
    ```

## Step 2: Configure environment variables

Create a `.env` file with the required settings:

```bash
# Required
HOST_URL=https://your-domain.com
SECRET_KEY=$(openssl rand -hex 16)
API_PASSWORD=your_strong_password
CONTACT_EMAIL=you@example.com
POSTGRES_URI=postgresql://user:password@localhost:5432/mediafusion

# Redis
REDIS_URL=redis://localhost:6379
```

See [Configuration Overview](../configuration/index.md) for all available options.

## Step 3: Start the API server

Database migrations run automatically on first startup:

```bash
export $(cat .env | xargs)
./mediafusion-api
```

Or with a process manager like `systemd`, source your `.env` file before starting.

## Step 4: Start the background worker

In a second terminal (or as a separate service):

```bash
export $(cat .env | xargs)
./mediafusion-worker
```

!!! tip "systemd example"
    Create a service file at `/etc/systemd/system/mediafusion-api.service`:

    ```ini
    [Unit]
    Description=MediaFusion API
    After=network.target postgresql.service redis.service

    [Service]
    EnvironmentFile=/path/to/.env
    ExecStart=/path/to/mediafusion-api
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    ```

    Then `systemctl enable --now mediafusion-api`.

---

## Checking migration status

Set `MEDIAFUSION_MIGRATE=status` to print the current schema version and exit:

```bash
MEDIAFUSION_MIGRATE=status ./mediafusion-api
```

For rollback instructions see [Database Migrations](migrations.md).
