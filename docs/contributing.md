# Contributing

Thank you for contributing to MediaFusion!

## Codebase overview

| Component | Language | Location |
|---|---|---|
| API server + background worker | Rust (Axum, Tokio) | `backend/` |
| Web UI (admin + configure) | React / TypeScript | `clients/frontend/` |
| Kodi addon | Python | `clients/kodi/` |
| Browser extension | JavaScript | `clients/browser-extension/` |

The Rust backend is the heart of the project. `python-deprecated/` contains the old Python API — it is archived and not active.

---

## Development setup

Full instructions are in the [Local Development guide](deployment/local-dev.md). Summary:

```bash
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion

# Start databases
cd deployment/docker-compose && docker compose -f docker-compose-minimal.yml up -d && cd ../..

# Create .env (see Local Dev guide for required values)

# Run the API with hot-reload
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

### TypeScript (frontend)

```bash
cd clients/frontend
npm run lint
npm run format
```

### Python (Kodi addon only)

```bash
uv run ruff format clients/kodi/
uv run ruff check clients/kodi/
```

---

## Running tests

```bash
# Rust
cargo test --manifest-path backend/Cargo.toml

# Python (Kodi addon)
uv run pytest
```

---

## Adding a new scraper

All scrapers live in the Rust backend:

- **Scheduled spider** (e.g. a new torrent site): add a handler in `backend/src/jobs/handlers/spiders/`, register it in `backend/src/bin/worker.rs`, and add a `DISABLE_*_SCHEDULER` env var to `backend/src/config.rs`.
- **Live search source** (queried on stream requests): implement the scraper trait in `backend/src/scrapers/` and register it in the orchestrator.
- **Config flag**: add an `IS_SCRAP_FROM_*` field in `backend/src/config.rs` with a `from_env()` binding.
- **Document it**: add a row to the scraper table in [Content Sources](configuration/content-sources.md) and the scheduler table in [Environment Variables](reference/env-reference.md).

---

## Submitting changes

1. **Fork** the repository
2. **Create a branch**: `git checkout -b my-feature`
3. **Make focused changes** — one PR per feature/fix
4. **Run tests and linters** before pushing
5. **Open a pull request** against `main`

PR checklist:
- [ ] `cargo fmt` and `cargo clippy` pass
- [ ] `cargo test` passes
- [ ] New env vars are documented in `backend/src/config.rs` (doc comments) and `docs/reference/env-reference.md`
- [ ] PR description explains *why*, not just *what*

---

## Commit message style

```
<type>: <short summary>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

Examples:
```
feat: add Byparr support for Cloudflare-protected indexers
fix: return 404 on unknown stream type instead of 500
docs: update env reference for DMM hashlist variables
```

---

## Reporting bugs

Search [GitHub Issues](https://github.com/mhdzumair/MediaFusion/issues) before opening a new one. Include:

- MediaFusion version
- Deployment method
- Steps to reproduce
- Relevant logs: `docker compose logs mediafusion --tail 100`

## License

By contributing, you agree your contributions are licensed under the MIT License.
