# Python API — Status Notice

## What's ACTIVE (used in production)

| File | Role |
|------|------|
| `task_queue.py` | Task dispatch infrastructure — imported by all background workers |
| `taskiq_worker.py` | Worker process entry point |
| `scheduler.py` | APScheduler cron job definitions |

## What's REFERENCE ONLY (not served in production)

Everything else in this directory (`app.py`, `main.py`, `routers/`, `middleware.py`, etc.) is the original FastAPI server. It is kept for reference but **not deployed**. The production API is the Rust service at `services/api/`.

## Production API

See `services/api/` for the active Rust API server.
