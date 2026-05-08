# MediaFusion Deployment Guide 🚀

Welcome to the deployment guide for MediaFusion! This document will help you navigate through the different deployment methods available for MediaFusion. Depending on your preference or environment constraints, you can choose between Kubernetes-based deployment, Docker Compose, or local development with partial Docker.

## Deployment Options 🛠️

MediaFusion supports multiple deployment strategies to cater to different infrastructure needs and preferences:

| Method | Best For |
|--------|----------|
| [Local Development](#local-development-) | Active development with hot-reload |
| [Docker Compose](./docker-compose/README.md) | Simple deployments & testing |
| [Kubernetes](./k8s/README.md) | Production & scalable environments |

## Local Development 💻

This section covers running MediaFusion locally with **partial Docker** (databases only) and the **Rust API server** — ideal for active development.

### Prerequisites

Ensure the following tools are installed:

- **Rust 1.88+**: Required for the API server. [Installation guide](https://rustup.rs/)
- **Python 3.12+**: Required for running background workers and migrations
- **uv**: Fast Python package installer. [Installation guide](https://docs.astral.sh/uv/getting-started/installation/)
- **Docker & Docker Compose**: For running databases. [Installation guide](https://docs.docker.com/get-docker/)

### Step 1: Clone & Setup

```bash
# Clone the repository
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion

# Install Python dependencies (workers + migrations)
uv sync
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

### Step 4: Run Database Migrations

```bash
# Run Alembic migrations for PostgreSQL
uv run alembic upgrade head
```

### Step 5: Start the Rust API Server

```bash
# Development build with auto-reload via cargo-watch (install once: cargo install cargo-watch)
cd services/api
cargo watch -x run

# Or run directly without hot-reload
cargo run

# Or use the Makefile shortcut from the repo root
make rust-dev
```

The server will be available at **http://127.0.0.1:8000** 🎉

> [!TIP]
> For a release build (faster, closer to production): `cargo run --release`

### Running Background Workers (Optional)

For testing scrapers and background tasks, run Taskiq workers in separate terminals from the **repo root**:

```bash
uv run taskiq worker workers.taskiq_worker:broker_default --workers 1 --max-async-tasks 8 --ack-type when_executed
uv run taskiq worker workers.taskiq_worker:broker_scrapy --workers 1 --max-async-tasks 1 --ack-type when_executed
uv run taskiq worker workers.taskiq_worker:broker_import --workers 1 --max-async-tasks 4 --ack-type when_executed
uv run taskiq worker workers.taskiq_worker:broker_priority --workers 1 --max-async-tasks 4 --ack-type when_executed
```

If you want a single worker to consume all queues (including Scrapy), set:

```bash
TASKIQ_SINGLE_WORKER_MODE=true
```

Then run only one worker:

```bash
uv run taskiq worker workers.taskiq_worker:broker_default --workers 1 --max-async-tasks 8 --ack-type when_executed
```

### Local Development Tips

- **Hot Reload**: Install `cargo-watch` (`cargo install cargo-watch`) and use `cargo watch -x run` for auto-restart on file changes
- **Debug Logging**: Set `LOGGING_LEVEL=DEBUG` for verbose Python worker output; the Rust server uses `RUST_LOG=debug`
- **Disable Schedulers**: Set `DISABLE_ALL_SCHEDULER=true` to prevent background tasks from running
- **Local Config**: Set `USE_CONFIG_SOURCE=local` to use local scraper configuration files
- **Rust Logs**: `RUST_LOG=mediafusion_api=debug,tower_http=debug cargo run`

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
- Python 3.12 or higher for background workers and migrations.
- uv (recommended) or pip for Python package management.

## Configuration 📝

All deployment methods require you to configure environment variables that are crucial for the operation of MediaFusion. These variables include API keys, database URIs, and other sensitive information which should be kept secure.

See the [Configuration Guide](/docs/env-reference.md) for detailed information on all available options.

## Support and Contributions 💡

Should you encounter any issues during deployment or have suggestions for improvement, please feel free to open an issue or pull request in our GitHub repository.

We welcome contributions and feedback to make MediaFusion better for everyone!

Happy Deploying! 🎉
