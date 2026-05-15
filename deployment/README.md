# MediaFusion Deployment Guide 🚀

Welcome to the deployment guide for MediaFusion! This document will help you navigate through the different deployment methods available for MediaFusion. Depending on your preference or environment constraints, you can choose between Kubernetes-based deployment, Docker Compose, direct binary deployment, or local development with partial Docker.

> [!IMPORTANT]
> **Always pin a specific version tag in production** (e.g. `mhdzumair/mediafusion:6.0.0-beta.5`). Never use `latest` or `beta` for production deployments.
> - `latest` — tracks the most recent **stable** release
> - `beta` — tracks the most recent **beta** release

## Deployment Options 🛠️

MediaFusion supports multiple deployment strategies to cater to different infrastructure needs and preferences:

| Method | Best For |
|--------|----------|
| [Direct Binary](#direct-binary-deployment-) | Minimal footprint, no Docker required |
| [Local Development](#local-development-) | Active development with hot-reload |
| [Docker Compose](./docker-compose/README.md) | Simple deployments & testing |
| [Kubernetes](./k8s/README.md) | Production & scalable environments |

---

## Direct Binary Deployment ⚡

Run MediaFusion with zero container overhead. The API server and worker are distributed as **statically compiled musl binaries** — no Docker, no system libraries, no runtime dependencies required. You only need PostgreSQL and Redis.

### Download

Binaries are published as GitHub Release assets. Grab the right pair for your architecture:

| Binary | amd64 | arm64 |
|--------|-------|-------|
| API server | `mediafusion-api-linux-amd64` | `mediafusion-api-linux-arm64` |
| Background worker | `mediafusion-worker-linux-amd64` | `mediafusion-worker-linux-arm64` |

```bash
# Example: download amd64 binaries for a specific release
RELEASE=6.0.0-beta.5
ARCH=amd64   # or arm64

curl -Lo mediafusion-api \
  "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-api-linux-${ARCH}"

curl -Lo mediafusion-worker \
  "https://github.com/mhdzumair/MediaFusion/releases/download/${RELEASE}/mediafusion-worker-linux-${ARCH}"

chmod +x mediafusion-api mediafusion-worker
```

### Running

1. Make sure PostgreSQL and Redis are reachable and set your environment variables (see [Configuration Guide](/docs/env-reference.md)).
2. Start the API server — **database migrations run automatically at startup**:

```bash
./mediafusion-api
```

3. In a second terminal (or as a service), start the background worker:

```bash
./mediafusion-worker
```

> [!TIP]
> Both binaries read the same environment variables as the Docker image. A minimal `.env` file (sourced with `export $(cat .env | xargs)` or a process manager like `systemd`) is sufficient.

> [!NOTE]
> See the [Migration Management](#migration-management-) section for how to check migration status or roll back before downgrading to an older release.

---

## Local Development 💻

This section covers running MediaFusion locally with **partial Docker** (databases only) and the **Rust API server** — ideal for active development.

### Prerequisites

Ensure the following tools are installed:

- **Rust 1.88+**: Required for the API server. [Installation guide](https://rustup.rs/)
- **Docker & Docker Compose**: For running databases. [Installation guide](https://docs.docker.com/get-docker/)

### Step 1: Clone & Setup

```bash
# Clone the repository
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion
```

### Step 2: Start Database Services

Start only the database services using the minimal Docker Compose configuration:

```bash
cd deployment/docker-compose

# Start PostgreSQL and Redis
docker compose -f docker-compose-minimal.yml up -d

# Verify services are running
docker compose -f docker-compose-minimal.yml ps
```

This starts:
- **PostgreSQL** on `localhost:5432` (user: `mediafusion`, password: `mediafusion`)
- **Redis** on `localhost:6379`

### Step 3: Configure Environment Variables

Create a `.env` file in the project root:

```bash
# Generate a secret key
SECRET_KEY=$(openssl rand -hex 16)

# Create .env file with essential configuration
cat > .env << EOF
# Core Settings
HOST_URL=http://127.0.0.1:8000
SECRET_KEY=${SECRET_KEY}
API_PASSWORD=dev_password
STREAM_RS_PORT=8000        # Rust server port (default: 8000)

# Database URIs (matching docker-compose-minimal.yml)
POSTGRES_URI=postgresql://mediafusion:mediafusion@localhost:5432/mediafusion
REDIS_URL=redis://localhost:6379

# Development Settings
LOGGING_LEVEL=DEBUG
USE_CONFIG_SOURCE=local

# Optional: Disable schedulers during development
DISABLE_ALL_SCHEDULER=true
EOF
```

> [!TIP]
> See [Configuration Guide](/docs/env-reference.md) for all available options.

### Step 4: Start the Rust API Server

Database migrations run automatically when the server starts — no separate migration step needed.

```bash
# Development build with auto-reload via cargo-watch (install once: cargo install cargo-watch)
cargo watch --manifest-path backend/Cargo.toml -x 'run --bin mediafusion-api'

# Or run directly without hot-reload
cargo run --manifest-path backend/Cargo.toml --bin mediafusion-api

# Or use the Makefile shortcut from the repo root
make rust-dev
```

The server will be available at **http://127.0.0.1:8000** 🎉

> [!TIP]
> For a release build (faster, closer to production): `cargo run --manifest-path backend/Cargo.toml --bin mediafusion-api --release`

### Running Background Workers (Optional)

For testing scrapers and background tasks, run the Rust worker in a separate terminal from the **repo root**:

```bash
cargo run --manifest-path backend/Cargo.toml --bin mediafusion-worker
```

### Local Development Tips

- **Hot Reload**: Install `cargo-watch` (`cargo install cargo-watch`) and use `cargo watch --manifest-path backend/Cargo.toml -x 'run --bin mediafusion-api'` for auto-restart on file changes
- **Debug Logging**: Set `RUST_LOG=mediafusion_api=debug,tower_http=debug` for verbose Rust output
- **Disable Schedulers**: Set `DISABLE_ALL_SCHEDULER=true` to prevent background tasks from running
- **Local Config**: Set `USE_CONFIG_SOURCE=local` to use local scraper configuration files
- **Rust Logs**: `RUST_LOG=mediafusion_api=debug,tower_http=debug cargo run --manifest-path backend/Cargo.toml --bin mediafusion-api`

### Stopping Services

```bash
# Stop the Rust server: Ctrl+C

# Stop database containers
cd deployment/docker-compose
docker compose -f docker-compose-minimal.yml down

# Remove volumes (reset databases)
docker compose -f docker-compose-minimal.yml down -v
```

---

## Kubernetes Deployment 🌐

For those using Kubernetes, we provide a detailed guide for deploying MediaFusion with Minikube, which is ideal for local development and testing. The Kubernetes deployment guide includes instructions on setting up secrets, generating SSL certificates, and configuring services.

👉 [Kubernetes Deployment Guide](./k8s/README.md)

## Docker Compose Deployment 🐳

If you're looking for a quick and straightforward full deployment, Docker Compose might be the right choice for you. Our Docker Compose guide outlines the steps for setting up MediaFusion on your local machine without the complexity of Kubernetes.

👉 [Docker Compose Deployment Guide](./docker-compose/README.md)

## Prerequisites 📋

Before proceeding with any deployment method, make sure you have the required tools installed on your system:

- Docker and Docker Compose for container management and orchestration.
- Kubernetes CLI (kubectl) if you are deploying with Kubernetes.
- Rust 1.88+ for building the API server locally (`rustup update stable`).

## Configuration 📝

All deployment methods require you to configure environment variables that are crucial for the operation of MediaFusion. These variables include API keys, database URIs, and other sensitive information which should be kept secure.

See the [Configuration Guide](/docs/env-reference.md) for detailed information on all available options.

---

## Migration Management 🗄️

### How migrations work

Database migrations are managed with **sqlx** and apply automatically at API or worker startup. There is no separate migration command to run — simply start the binary and the database will be brought up to date.

### Checking migration status

Set the `MEDIAFUSION_MIGRATE` environment variable to `status` and run either binary. It will print the current migration table and exit without starting the server:

```bash
MEDIAFUSION_MIGRATE=status ./mediafusion-api
# or with Docker:
docker run --rm --env-file .env -e MEDIAFUSION_MIGRATE=status mhdzumair/mediafusion:6.0.0-beta.5
```

### Rolling back migrations

Set `MEDIAFUSION_MIGRATE_ROLLBACK_TO=<version>` and run either binary. It will roll the database back to the specified version and exit:

```bash
# Roll back to version 4 before downgrading the Docker image
MEDIAFUSION_MIGRATE_ROLLBACK_TO=4 ./mediafusion-api
# or with Docker (run the current/newer image to roll back):
docker run --rm --env-file .env \
  -e MEDIAFUSION_MIGRATE_ROLLBACK_TO=4 \
  mhdzumair/mediafusion:6.0.0-beta.5
```

> [!WARNING]
> If you ran a beta that applied new migrations, you **must** roll back before downgrading to an older image or binary. The older binary will refuse to start if it finds schema versions it does not recognise.

### Downgrading from 6.x beta to 5.x

5.x used **Alembic** for migrations, not sqlx. After rolling back with the 6.x beta binary, you also need to restore the Alembic version marker so that 5.x knows the schema state:

1. Roll back with the 6.x binary as described above.
2. Connect to PostgreSQL and run:

```sql
UPDATE alembic_version SET version_num = 'd826df80371b';
```

3. Start the 5.x binary or Docker image — it will see the correct Alembic revision and start normally.

---

## Support and Contributions 💡

Should you encounter any issues during deployment or have suggestions for improvement, please feel free to open an issue or pull request in our GitHub repository.

We welcome contributions and feedback to make MediaFusion better for everyone!

Happy Deploying! 🎉
