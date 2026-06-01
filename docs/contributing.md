# Contributing

Thank you for contributing to MediaFusion! This guide covers the development setup, code style expectations, and how to submit changes.

## Architecture quick-reference

MediaFusion is a **Rust + Python** project:

| Component | Language | Location |
|---|---|---|
| API server (main HTTP server) | Rust (Axum) | `backend/src/` |
| Background worker (scrapers, tasks) | Rust (Taskiq) | `backend/src/bin/mediafusion-worker.rs` |
| Web scrapers (Scrapy spiders) | Python | `api/scrapers/` |
| Frontend (React SPA) | TypeScript | `clients/frontend/` |
| Kodi addon | Python | `clients/kodi/` |
| Browser extension | JavaScript | `clients/browser-extension/` |

---

## Development setup

Full instructions are in the [Local Development guide](deployment/local-dev.md). Summary:

```bash
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion

# 1. Start databases
cd deployment/docker-compose && docker compose -f docker-compose-minimal.yml up -d && cd ../..

# 2. Create .env (see Local Dev guide for required values)

# 3. Run the Rust API with hot-reload
cargo watch --manifest-path backend/Cargo.toml -x 'run --bin mediafusion-api'
```

---

## Code style

### Rust

```bash
# Format
cargo fmt --manifest-path backend/Cargo.toml

# Lint
cargo clippy --manifest-path backend/Cargo.toml
```

### Python

```bash
# Format and lint with ruff
uv run ruff format .
uv run ruff check .
```

### TypeScript (frontend)

```bash
cd clients/frontend
npm run lint
npm run format
```

---

## Running tests

```bash
# Python tests
uv run pytest

# Rust tests
cargo test --manifest-path backend/Cargo.toml
```

---

## Adding a new scraper

1. **Background (scheduled) scraper**: add a Scrapy spider in `api/scrapers/`, register it in the scheduler in `backend/src/jobs/`.
2. **Live search source**: implement the `LiveScraper` trait in `backend/src/scrapers/` and register it in the scraper registry.
3. **Config flag**: add a new `IS_SCRAP_FROM_*` env var and field in `backend/src/config.rs`.
4. **Document it**: add a row to the scraper table in [Content Sources](configuration/content-sources.md) and the scheduler table in [Environment Variables](reference/env-reference.md).

---

## Submitting changes

1. **Fork** the repository
2. **Create a branch**: `git checkout -b my-feature`
3. **Make focused changes** — one PR per feature/fix
4. **Run tests and linters** before pushing
5. **Open a pull request** against `main`

PR checklist:
- [ ] Tests pass (`pytest` and `cargo test`)
- [ ] Code is formatted (`ruff format`, `cargo fmt`)
- [ ] New env vars are documented in `backend/src/config.rs` (doccomments) and in `docs/reference/env-reference.md`
- [ ] PR description explains *why*, not just *what*

---

## Reporting bugs

Search [GitHub Issues](https://github.com/mhdzumair/MediaFusion/issues) before opening a new one.

Include:
- MediaFusion version (`docker inspect` or binary `--version`)
- Deployment method (Docker Compose, binary, etc.)
- Steps to reproduce
- Relevant logs:
  ```bash
  docker compose logs mediafusion --tail 100
  ```

## Suggesting features

Open a GitHub Issue with a clear title, description, and concrete use case. Link to any related issues or prior discussion.

---

## Commit message style

```
<type>: <short summary>

<body — explain the why, not the what>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

Examples:
```
feat: add Byparr support for Cloudflare-protected indexers
fix: return 404 on unknown stream type instead of 500
docs: update env reference for new DMM hashlist variables
```

---

## License

By contributing, you agree your contributions are licensed under the MIT License.
