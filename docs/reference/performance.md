# Performance Tuning

Reference guide for diagnosing and tuning MediaFusion's Rust backend.

---

## Architecture summary

```
Stremio / Kodi clients
        │
   (HTTPS/HTTP)
        │
  mediafusion-api        ← single Rust binary, Axum + Tokio async runtime
  (jemalloc allocator)
        │
  Middleware stack
   ├─ RequestId           → X-Request-Id header + log injection
   ├─ TransientRetry      → retries idempotent requests on transient DB errors
   ├─ SecureLogging       → masks secret_str in access logs
   ├─ Timing              → X-Process-Time + Prometheus histogram
   ├─ UserData            → AES-256 manifest decryption (cached in Redis)
   ├─ APIKey              → validates X-API-Key on private instances
   └─ RateLimit           → per-IP Redis token bucket
        │
  Route handlers
   ├─ /stream/...         ← MOST EXPENSIVE: DB + Redis MGET + live-scraper fanout
   ├─ /playback/...       ← HEAVY: debrid API calls, Redis lock
   ├─ /meta/...           ← MEDIUM: Redis cache, DB query
   ├─ /catalog/...        ← MEDIUM: Redis cache, DB query
   ├─ /manifest.json      ← CHEAP: HMAC + Redis GET
   └─ /poster/...         ← CPU: image compositing (image + imageproc crates)
        │
  ┌─────────────┐   ┌────────────────┐
  │ PostgreSQL  │   │     Redis      │
  │ (+ PgBouncer│   │ (stream cache, │
  │  replicas)  │   │  rate limits,  │
  └─────────────┘   │  task queue)   │
                    └────────────────┘
                           │
                  mediafusion-worker   ← background job binary
                  (scrapers, imports, maintenance)
```

---

## Rust vs Python performance

MediaFusion 6.0 replaced the Python/FastAPI/Gunicorn stack with a native Rust binary. Key differences that affect capacity planning:

| Aspect | Python (5.x) | Rust (6.x) |
|---|---|---|
| Concurrency model | Multiple Gunicorn worker processes (1 event loop each) | Single binary, Tokio async runtime (work-stealing thread pool) |
| Memory per instance | ~200–400 MB per worker process | ~50–100 MB total |
| Connection pool | SQLAlchemy pool per Gunicorn worker | sqlx pool shared across async tasks |
| Request latency (cache hit) | ~300–500 ms | ~5–50 ms |
| Image generation | Python Pillow in thread pool | Rust `image` + `imageproc`, inline |
| Cold-start time | 3–10 s (Python import) | < 1 s |

---

## Key environment variables

| Variable | Default | Effect |
|---|---|---|
| `POSTGRES_URI` | required | Primary PostgreSQL connection string |
| `POSTGRES_READ_URI` | — | If set, read queries route to replica |
| `REDIS_URL` | `redis://...` | Redis connection URL |
| `META_CACHE_TTL_SECONDS` | `1800` | Redis TTL for meta/catalog responses |
| `CATALOG_CACHE_TTL_SECONDS` | `1800` | Redis TTL for catalog listings |
| `STREAM_RAW_REDIS_CACHE_TTL_SECONDS` | `900` | Redis TTL for stream blobs |
| `REQUEST_TIMEOUT` | `120` | Timeout in seconds for `/stream/` routes |
| `ENABLE_PROMETHEUS_METRICS` | `false` | Expose `/api/v1/metrics` |
| `PROMETHEUS_METRICS_TOKEN` | — | Bearer token to protect the metrics endpoint |
| `BACKGROUND_SEARCH_ENABLED` | `true` | Enable background re-scraping queue |

---

## Observability

### Prometheus metrics

Enable with `ENABLE_PROMETHEUS_METRICS=true`. Scrape at `/api/v1/metrics`:

```bash
curl http://localhost:8000/api/v1/metrics
```

Key metrics:

| Metric | What it signals |
|---|---|
| `http_request_duration_seconds{route="/stream/..."}` | Stream resolution latency — primary user-visible signal |
| `http_requests_in_flight` | Concurrent requests; spikes = burst load |
| `http_requests_total{status_code="5xx"}` | Server errors — check DB connectivity and pool exhaustion |

Secure the endpoint on public instances with `PROMETHEUS_METRICS_TOKEN`.

### Per-route Redis metrics

```bash
curl -H "X-API-Key: $API_PASSWORD" \
  http://localhost:8000/api/v1/admin/metrics/request-metrics/endpoint-stats
```

Returns p50/p95/p99 per route from a rolling 1000-sample window. Also visible in the React admin UI at `/app` → Admin → Metrics.

### Slow query diagnostics

Requires `pg_stat_statements` (migration `0014`, enabled in Docker/K8s compose). On a **new** dev Postgres after updating `docker-compose-minimal.yml`, recreate the container so preload takes effect:

```bash
cd deployment/docker-compose
docker compose -f docker-compose-minimal.yml up -d --force-recreate postgres
```

Migrations run on API startup; or manually: `CREATE EXTENSION IF NOT EXISTS pg_stat_statements;`

**Performance impact:** Typically **~1–3% CPU** overhead with `pg_stat_statements.track=all` on a busy OLTP workload. Shared memory is bounded by `pg_stat_statements.max` (10 000 in our compose; on the order of a few MB). No measurable impact on query plans; safe for production when you want observability. Use `track=top` instead of `all` to reduce overhead further if needed.

```bash
# Top 20 slowest query fingerprints (requires pg_stat_statements extension)
curl -H "X-API-Key: $API_PASSWORD" \
  "http://localhost:8000/api/v1/admin/db/slow-queries?limit=20&order_by=mean_exec_time&min_mean_time_ms=100"

# Reset counters
curl -X POST -H "X-API-Key: $API_PASSWORD" \
  http://localhost:8000/api/v1/admin/db/slow-queries/reset
```

### Request tracing

Every request gets an `X-Request-Id` header injected into all log lines for that request. Trace a specific request:

```bash
grep "a1b2c3d4" /var/log/mediafusion.log
```

---

## Database connection sizing

sqlx manages a single async connection pool (unlike Python's per-process pool). The pool is sized by `DB_MAX_CONNECTIONS` divided across primary and optional read replica:

| Setup | Pool per engine |
|---|---|
| Primary only | `DB_MAX_CONNECTIONS` connections to primary |
| Primary + replica | `DB_MAX_CONNECTIONS / 2` connections to each |

If you see connection timeout errors, either increase `DB_MAX_CONNECTIONS` (only if PostgreSQL's `max_connections` allows headroom) or put PgBouncer in front and set `POSTGRES_USE_PGBOUNCER=true`.

**Check `pg_stat_activity` directly:**

```sql
SELECT state, wait_event_type, wait_event, left(query, 80) as query
FROM pg_stat_activity
WHERE datname = current_database()
ORDER BY query_start;
```

---

## Diagnosing high p95 on `/stream/...`

1. Check Prometheus: is `http_request_duration_seconds{route="/stream/..."}` p95 high only on cache misses or also on hits?
2. **Cache miss path**: live scrapers are running — check scraper health at `/app` → Admin → Scrapers.
3. **Cache hit path**: DB query or Redis latency — check `GET /api/v1/admin/db/slow-queries`.
4. Check Redis hit rate at `/app` → Admin → Metrics → Redis.
5. Check if the live-search lock (`live_search_lock:{id}`) is contended — multiple users hitting the same uncached title simultaneously.

---

## Load testing

See `perf/README.md` for full instructions. Quick start:

```bash
uv run locust -f perf/locustfile.py --headless -u 30 -r 5 -t 15m \
  --host http://localhost:8000 \
  --html perf/results/soak-$(date +%Y%m%d).html
```
