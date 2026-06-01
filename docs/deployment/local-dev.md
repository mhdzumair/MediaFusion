# Local Development Setup

For contributing to MediaFusion or running the latest code. Runs databases in Docker and the Rust API server natively for fast iteration.

## Prerequisites

- [Rust 1.88+](https://rustup.rs/) — `rustup update stable`
- [Docker & Docker Compose](https://docs.docker.com/get-docker/)
- `cargo-watch` for hot-reload (optional): `cargo install cargo-watch`

## Step 1: Clone and enter the repo

```bash
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion
```

## Step 2: Start the databases

```bash
cd deployment/docker-compose
docker compose -f docker-compose-minimal.yml up -d
docker compose -f docker-compose-minimal.yml ps
```

This starts:

| Service | Address |
|---|---|
| PostgreSQL | `localhost:5432` (user: `mediafusion`, password: `mediafusion`) |
| Redis | `localhost:6379` |

## Step 3: Create a `.env` file

Create `.env` in the **project root**:

```bash
SECRET_KEY=$(openssl rand -hex 16)

cat > .env << EOF
HOST_URL=http://127.0.0.1:8000
SECRET_KEY=${SECRET_KEY}
API_PASSWORD=dev_password
CONTACT_EMAIL=dev@example.com

POSTGRES_URI=postgresql://mediafusion:mediafusion@localhost:5432/mediafusion
REDIS_URL=redis://localhost:6379

LOGGING_LEVEL=DEBUG
USE_CONFIG_SOURCE=local
DISABLE_ALL_SCHEDULER=true
EOF
```

Key dev settings:

| Variable | Value | Effect |
|---|---|---|
| `USE_CONFIG_SOURCE` | `local` | Use local scraper config files instead of database |
| `DISABLE_ALL_SCHEDULER` | `true` | Prevent background scrapers from running automatically |
| `LOGGING_LEVEL` | `DEBUG` | Verbose logging |

## Step 4: Run the API server

Database migrations run automatically on first start.

=== "With hot-reload (recommended)"

    ```bash
    cargo watch --manifest-path backend/Cargo.toml -x 'run --bin mediafusion-api'
    ```

=== "Without hot-reload"

    ```bash
    cargo run --manifest-path backend/Cargo.toml --bin mediafusion-api
    ```

=== "Using the Makefile"

    ```bash
    make rust-dev
    ```

The server starts at **http://127.0.0.1:8000**.

## Step 5: Run the background worker (optional)

In a separate terminal from the project root:

```bash
cargo run --manifest-path backend/Cargo.toml --bin mediafusion-worker
```

---

## Useful tips

**Verbose Rust logs:**
```bash
RUST_LOG=mediafusion_api=debug,tower_http=debug cargo run --manifest-path backend/Cargo.toml --bin mediafusion-api
```

**Release build (faster, closer to production):**
```bash
cargo run --manifest-path backend/Cargo.toml --bin mediafusion-api --release
```

**Stop databases:**
```bash
cd deployment/docker-compose
docker compose -f docker-compose-minimal.yml down

# Remove volumes (full reset)
docker compose -f docker-compose-minimal.yml down -v
```

## Running tests

```bash
uv run pytest
```

See [Contributing](../contributing.md) for the full contribution workflow.
