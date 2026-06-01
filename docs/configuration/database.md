# Database & Redis

MediaFusion uses PostgreSQL as the primary database and Redis for caching, task queuing, and rate limiting.

## PostgreSQL

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_URI` | **required** | Primary read-write connection string |
| `POSTGRES_READ_URI` | `None` | Optional read-replica URI. If set, read queries route here. |
| `POSTGRES_USE_PGBOUNCER` | `false` | Set `true` when `POSTGRES_URI` points at PgBouncer in transaction mode |
| `DB_MAX_CONNECTIONS` | `50` | Total SQLAlchemy connection budget per instance |

### Connection string formats

Both formats are accepted — the backend normalises them automatically:

```bash
# Standard format
POSTGRES_URI=postgresql://user:password@host:5432/mediafusion

# SQLAlchemy format (also accepted)
POSTGRES_URI=postgresql+asyncpg://user:password@host:5432/mediafusion
```

### Connection pool sizing

`DB_MAX_CONNECTIONS` is divided by `GUNICORN_WORKERS` and by 2 if `POSTGRES_READ_URI` is set.

For example: `DB_MAX_CONNECTIONS=50`, `GUNICORN_WORKERS=3`, with read replica → each worker gets `50 / 3 / 2 ≈ 8` connections.

If you see `QueuePool limit ... connection timed out`, either increase `DB_MAX_CONNECTIONS` (only if PostgreSQL's `max_connections` allows it) or reduce `GUNICORN_WORKERS`.

### PgBouncer

When using PgBouncer in **transaction mode**, set `POSTGRES_USE_PGBOUNCER=true`. This disables asyncpg's prepared-statement cache, which is incompatible with transaction-mode pooling.

### Managed PostgreSQL (recommended for production)

For production, use a managed service instead of in-cluster Postgres:

- AWS RDS for PostgreSQL
- Google Cloud SQL
- Azure Database for PostgreSQL
- DigitalOcean Managed Databases
- Supabase

Just set `POSTGRES_URI` (and optionally `POSTGRES_READ_URI`) to the connection strings from your managed service.

---

## Redis

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://redis-service:6379` | Redis connection URL |
| `REDIS_MAX_CONNECTIONS` | `100` | Per-process Redis connection pool size |
| `REDIS_RETRY_ATTEMPTS` | `3` | Retry attempts on transient errors |
| `REDIS_RETRY_DELAY` | `0.1` | Delay in seconds between retries |
| `REDIS_CONNECTION_TIMEOUT` | `10` | Connection timeout in seconds |
| `REDIS_ENABLE_CIRCUIT_BREAKER` | `true` | Open circuit on repeated Redis failures |

### Redis URL for the Rust components

| Variable | Default | Description |
|---|---|---|
| `REDIS_RS_URL` | `None` | Separate Redis URL for the Rust binary. Falls back to `REDIS_URL` if not set. |

---

## Performance workers

| Variable | Default | Description |
|---|---|---|
| `GUNICORN_WORKERS` | `3` | Number of Uvicorn worker processes |
| `GUNICORN_TIMEOUT` | `120` | Request timeout in seconds |
| `GUNICORN_MAX_REQUESTS` | `5000` | Restart a worker after this many requests (prevents memory leaks) |
| `GUNICORN_MAX_REQUESTS_JITTER` | `2000` | Random jitter on top of `MAX_REQUESTS` |
| `TASKIQ_SINGLE_WORKER_MODE` | `true` | Route all task queues into one worker process |

Set `TASKIQ_SINGLE_WORKER_MODE=false` for HA deployments where you run dedicated worker processes per queue (`default`, `scrapy`, `import`, `priority`).
