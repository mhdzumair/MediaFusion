# Direct Binary Deployment

Run MediaFusion with zero container overhead. The API server and background worker are distributed as **pre-built release binaries** — no Docker, no runtime dependencies. You only need PostgreSQL and Redis.

Linux builds are **statically linked musl binaries**. macOS and Windows builds are native release binaries produced with the same CI pipeline.

## Prerequisites

- **PostgreSQL 14+** — primary database
- **Redis 6+** — cache and task queue
- A publicly accessible URL for your instance

## Step 1: Download the binaries

Binaries are published as GitHub Release assets. Find the latest release at [github.com/mhdzumair/MediaFusion/releases](https://github.com/mhdzumair/MediaFusion/releases).

Asset names follow `mediafusion-{api|worker}-{platform}-{arch}` (`.exe` on Windows).

| Platform | Architecture | API asset | Worker asset |
|----------|--------------|-----------|--------------|
| Linux | amd64 | `mediafusion-api-linux-amd64` | `mediafusion-worker-linux-amd64` |
| Linux | arm64 | `mediafusion-api-linux-arm64` | `mediafusion-worker-linux-arm64` |
| macOS | amd64 (Intel) | `mediafusion-api-macos-amd64` | `mediafusion-worker-macos-amd64` |
| macOS | arm64 (Apple Silicon) | `mediafusion-api-macos-arm64` | `mediafusion-worker-macos-arm64` |
| Windows | amd64 | `mediafusion-api-windows-amd64.exe` | `mediafusion-worker-windows-amd64.exe` |

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

=== "macOS amd64 (Intel)"

    ```bash
    RELEASE=6.0.0   # replace with the latest release tag
    ARCH=amd64

    curl -Lo mediafusion-api \
      "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-api-macos-${ARCH}"

    curl -Lo mediafusion-worker \
      "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-worker-macos-${ARCH}"

    chmod +x mediafusion-api mediafusion-worker
    ```

=== "macOS arm64 (Apple Silicon)"

    ```bash
    RELEASE=6.0.0   # replace with the latest release tag
    ARCH=arm64

    curl -Lo mediafusion-api \
      "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-api-macos-${ARCH}"

    curl -Lo mediafusion-worker \
      "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-worker-macos-${ARCH}"

    chmod +x mediafusion-api mediafusion-worker
    ```

=== "Windows amd64"

    ```powershell
    $Release = "6.0.0"   # replace with the latest release tag
    $Base = "https://github.com/mhdzumair/MediaFusion/releases/download/$Release"

    Invoke-WebRequest -Uri "$Base/mediafusion-api-windows-amd64.exe" -OutFile mediafusion-api.exe
    Invoke-WebRequest -Uri "$Base/mediafusion-worker-windows-amd64.exe" -OutFile mediafusion-worker.exe
    ```

!!! note "macOS Gatekeeper"
    On first launch, macOS may block unsigned binaries. Allow them in **System Settings → Privacy & Security**, or remove the quarantine attribute:

    ```bash
    xattr -dr com.apple.quarantine mediafusion-api mediafusion-worker
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

=== "Linux / macOS"

    Load variables into your shell before starting:

    ```bash
    set -a && source .env && set +a
    ```

=== "Windows (PowerShell)"

    ```powershell
    Get-Content .env | ForEach-Object {
      if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
      }
    }
    ```

## Step 3: Start the API server

Database migrations run automatically on first startup:

=== "Linux / macOS"

    ```bash
    ./mediafusion-api
    ```

=== "Windows"

    ```powershell
    .\mediafusion-api.exe
    ```

Or with a process manager (`systemd` on Linux, `launchd` on macOS, Windows Service).

## Step 4: Start the background worker

In a second terminal (or as a separate service):

=== "Linux / macOS"

    ```bash
    ./mediafusion-worker
    ```

=== "Windows"

    ```powershell
    .\mediafusion-worker.exe
    ```

!!! tip "systemd example (Linux)"
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

=== "Linux / macOS"

    ```bash
    MEDIAFUSION_MIGRATE=status ./mediafusion-api
    ```

=== "Windows"

    ```powershell
    $env:MEDIAFUSION_MIGRATE = "status"
    .\mediafusion-api.exe
    ```

For rollback instructions see [Database Migrations](migrations.md).
