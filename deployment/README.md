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

This section covers running MediaFusion locally with **partial Docker** (databases only) and **uvicorn with SSL support** — ideal for active development with hot-reload capabilities.

### Prerequisites

Ensure the following tools are installed:

- **Python 3.12+**: Required for running the application
- **uv**: Fast Python package installer. [Installation guide](https://docs.astral.sh/uv/getting-started/installation/)
- **Docker & Docker Compose**: For running databases. [Installation guide](https://docs.docker.com/get-docker/)
- **mkcert**: For generating self-signed SSL certificates. [Installation guide](https://github.com/FiloSottile/mkcert?tab=readme-ov-file#installation)

### Step 1: Clone & Setup

```bash
# Clone the repository
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion

# Install Python dependencies using uv
uv sync
```

### Step 2: Start Database Services

Start only the database services using the minimal Docker Compose configuration:

```bash
cd deployment/docker-compose

# Start MongoDB, PostgreSQL, and Redis
docker compose -f docker-compose-minimal.yml up -d

# Verify services are running
docker compose -f docker-compose-minimal.yml ps
```

This starts:
- **MongoDB** on `localhost:27017`
- **PostgreSQL** on `localhost:5432` (user: `mediafusion`, password: `mediafusion`)
- **Redis** on `localhost:6379`

### Step 3: Generate SSL Certificates

MediaFusion requires HTTPS for certain features. Generate self-signed certificates:

```bash
# Install mkcert root CA (one-time setup)
mkcert -install

# Generate certificates for local development
mkcert -key-file key.pem -cert-file cert.pem localhost 127.0.0.1 ::1 mediafusion.local
```

> [!TIP]
> If using WSL, also run `mkcert -install` in Windows PowerShell to install the root certificate.

### Step 4: Configure Environment Variables

Create a `.env` file in the project root:

```bash
# Generate a secret key
SECRET_KEY=$(openssl rand -hex 16)

# Create .env file with essential configuration
cat > .env << EOF
# Core Settings
HOST_URL=https://127.0.0.1:8443
SECRET_KEY=${SECRET_KEY}
API_PASSWORD=dev_password

# Database URIs (matching docker-compose-minimal.yml)
MONGO_URI=mongodb://localhost:27017/mediafusion
POSTGRES_URI=postgresql+asyncpg://mediafusion:mediafusion@localhost:5432/mediafusion
REDIS_URL=redis://localhost:6379

# Development Settings
LOGGING_LEVEL=DEBUG
USE_CONFIG_SOURCE=local

# Optional: Disable schedulers during development
DISABLE_ALL_SCHEDULER=true
EOF
```

> [!TIP]
> See [Configuration Guide](/docs/configuration.md) for all available options.

### Step 5: Run Database Migrations

```bash
# Run Beanie migrations for MongoDB
uv run beanie migrate -uri "mongodb://localhost:27017" -db mediafusion -p migrations/

# Run Alembic migrations for PostgreSQL (if using PostgreSQL)
uv run alembic upgrade head
```

### Step 6: Start the Development Server

Run uvicorn with SSL support and hot-reload:

```bash
uv run uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 8443 \
    --ssl-keyfile key.pem \
    --ssl-certfile cert.pem \
    --reload
```

The server will be available at **https://127.0.0.1:8443** 🎉

### Quick Start Script (Optional)

Create a `dev.sh` script for convenience:

```bash
#!/bin/bash
set -e

# Start databases if not running
cd deployment/docker-compose
docker compose -f docker-compose-minimal.yml up -d
cd ../..

# Run the development server
uv run uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 8443 \
    --ssl-keyfile key.pem \
    --ssl-certfile cert.pem \
    --reload
```

### Running Background Workers (Optional)

For testing scrapers and background tasks, run Dramatiq workers in a separate terminal:

```bash
uv run dramatiq api.task scrapers.scraper_tasks mediafusion_scrapy.task -p 1 -t 1
```

### Local Development Tips

- **Hot Reload**: The `--reload` flag automatically restarts the server on code changes
- **Debug Logging**: Set `LOGGING_LEVEL=DEBUG` for verbose output
- **Disable Schedulers**: Set `DISABLE_ALL_SCHEDULER=true` to prevent background tasks from running
- **Local Config**: Set `USE_CONFIG_SOURCE=local` to use local scraper configuration files

### Stopping Services

```bash
# Stop the uvicorn server: Ctrl+C

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
- Python 3.12 or higher for local development.
- uv (recommended) or pip for Python package management.

## Configuration 📝

All deployment methods require you to configure environment variables that are crucial for the operation of MediaFusion. These variables include API keys, database URIs, and other sensitive information which should be kept secure.

See the [Configuration Guide](/docs/configuration.md) for detailed information on all available options.

## Support and Contributions 💡

Should you encounter any issues during deployment or have suggestions for improvement, please feel free to open an issue or pull request in our GitHub repository.

We welcome contributions and feedback to make MediaFusion better for everyone!

Happy Deploying! 🎉
