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

### Alternative: build the image in Docker

If you would rather not install Rust and Node on the host, `deployment/Dockerfile.local` builds everything inside the image. It builds natively, so it behaves the same on arm64, but a release build of the whole dependency tree takes a while there.

From the project root:

```bash
cd deployment/docker-compose
cp .env-sample .env
```

Compose resolves `env_file: .env` relative to the compose file, so the container reads this `.env` and not the one from Step 3. Adjust the copy before starting: the sample ships an empty `SECRET_KEY` and `API_PASSWORD`, so set both, point `HOST_URL` at `http://127.0.0.1:8000`, and add the dev settings from Step 3 (`USE_CONFIG_SOURCE=local`, `DISABLE_ALL_SCHEDULER=true`, `LOGGING_LEVEL=DEBUG`). Then:

```bash
docker compose -f docker-compose-minimal.yml -f docker-compose-local.yml up -d --build
docker compose -f docker-compose-minimal.yml -f docker-compose-local.yml logs -f mediafusion-api
```

Prefix the command with `PROFILE=dev` for a debug build that compiles several times faster, which matters on arm64.

The first build compiles everything and can take various minutes;
later builds reuse the cargo and pnpm caches.

This takes over the API service from step 2, so stop that one first to free port 8000:

```bash
docker compose -f docker-compose-minimal.yml stop mediafusion-api
```

## Step 5: Run the background worker (optional)

In a separate terminal from the project root:

```bash
cargo run --manifest-path backend/Cargo.toml --bin mediafusion-worker
```

### Optional: TRAWL for Cloudflare scrapers

The minimal dev stack (`docker-compose-minimal.yml`) does not include TRAWL. Scrapers for ext.to, sport-video, and CF-protected public indexers need it.

Run TRAWL against the dev Redis (database 1):

```bash
docker run -d --name trawl --network host --shm-size=1g \
  -e REDIS_URL=redis://127.0.0.1:6379/1 \
  -e BROWSER_POOL_SIZE=2 \
  -p 8191:8191 \
  ghcr.io/germondai/trawl:latest
```

Add to your root `.env`:

```bash
TRAWL_URL=http://127.0.0.1:8191
```

Test a single ext.to page scrape:

```bash
cargo run --manifest-path backend/Cargo.toml --bin mediafusion-worker \
  -- --run-job spider_movies_tv_ext --args '{"total_pages": 1}'
```

See [Worker CLI Reference](worker-cli.md) for all job names.

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
