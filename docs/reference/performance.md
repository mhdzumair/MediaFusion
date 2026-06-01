# MediaFusion Performance Guide

This document is the living runbook for diagnosing and tuning MediaFusion's backend performance.

---

## Architecture overview (for capacity planning)

```
Stremio clients
      │
 (HTTPS/HTTP)
      │
 gunicorn (UvicornWorker)   ← GUNICORN_WORKERS processes, each with 1 asyncio loop
      │
 FastAPI middleware stack
  ├─ RequestIdMiddleware      → assigns X-Request-Id, injects into log records
  ├─ TransientDatabaseRetry   → retries idempotent requests on transient DB errors
  ├─ SecureLoggingMiddleware  → masks secret_str in access logs
  ├─ TimingMiddleware         → sets X-Process-Time, records to Redis + Prometheus
  ├─ UserDataMiddleware       → decrypts secret_str (LRU-cached) → request.scope["user"]
  ├─ APIKeyMiddleware         → validates X-API-Key on private instances
  └─ RateLimitMiddleware      → per-IP Redis token bucket
      │
 Route handlers
  ├─ /stream/...              ← MOST EXPENSIVE: DB + Redis MGET + optional live-scraper fanout
  ├─ /playback/...            ← HEAVY: debrid API calls, Redis lock, fire-and-forget tracking
  ├─ /meta/...                ← MEDIUM: Redis cache, occasional DB selectinload
  ├─ /catalog/...             ← MEDIUM: Redis cache, occasional DB
  ├─ /manifest.json           ← CHEAP: HMAC + Redis GET
  └─ /poster/...              ← CPU: Pillow in run_in_executor
      │
 ┌──────────────────┐  ┌──────────────────┐
 │  PostgreSQL       │  │  Redis            │
 │  (+ PgBouncer)    │  │  (stream cache,   │
 │  primary+replica  │  │   rate limits,    │
 └──────────────────┘  │   task queue)     │
                        └──────────────────┘
      │
 Taskiq workers (separate processes)
  ← bulk scraping, feed processing, background imports
```

---

## Key environment variables

| Variable | Default | Effect |
|---|---|---|
| `GUNICORN_WORKERS` | 3 | Uvicorn worker processes. Each holds its own DB pool. |
| `DB_MAX_CONNECTIONS` | 50 | Total PG connection budget per app instance. Divided by workers and engines. |
| `POSTGRES_READ_URI` | unset | If set, read queries go to a replica; write queries stay on primary. |
| `POSTGRES_USE_PGBOUNCER` | false | Set `true` when `POSTGRES_URI` points at PgBouncer in transaction mode — disables asyncpg prepared-statement cache. |
| `REDIS_MAX_CONNECTIONS` | 100 | Per-process Redis pool size. |
| `ENABLE_REQUEST_METRICS` | true | Enables per-route p50/p95/p99 tracked in Redis. |
| `ENABLE_METRICS_ENDPOINT` | true | Exposes Prometheus `/metrics` endpoint. |
| `METRICS_BEARER_TOKEN` | unset | If set, `/metrics` requires `Authorization: Bearer <token>`. |
| `ENABLE_PROFILER` | false | Enables `?_profile=1` pyinstrument flamegraph (requires X-API-Key). Never enable in production. |
| `LOGGING_LEVEL` | INFO | Root log level. Set `DEBUG` only locally. |
| `LIVE_SEARCH_STREAMS` | true | When true, `/stream/...` fans out to N scrapers in parallel (adds 1–5s latency). |

---

## DB pool sizing math

With default settings and **no read replica**:
```
pool_size = floor((DB_MAX_CONNECTIONS / GUNICORN_WORKERS) * 0.75)
max_overflow = floor((DB_MAX_CONNECTIONS / GUNICORN_WORKERS) * 0.25)
```

With **read replica**:
```
budget = DB_MAX_CONNECTIONS / GUNICORN_WORKERS / 2   # split between primary and replica
pool_size = floor(budget * 0.75)
max_overflow = floor(budget * 0.25)
```

**Critical ceiling**: PostgreSQL `max_connections` limits total connections across all pods + workers + pgbouncer. At HPA max (5 pods × 3 workers × 2 engines × (pool_size + max_overflow)) you will exceed `max_connections=200`. Use PgBouncer in transaction mode to multiplex — see `deployment/docker-compose/docker-compose-postgres-ha.yml`.

---

## Observability stack

### 1. Prometheus metrics at `/metrics`

```bash
curl http://localhost:8000/metrics
```

Key metrics to watch:

| Metric | What it means |
|---|---|
| `http_request_duration_seconds{route="/stream/..."}` | Stream resolution latency — the main user-visible signal |
| `http_requests_in_flight` | Concurrent requests; spikes indicate burst load |
| `http_requests_total{status_code="503"}` | DB pool exhaustion — means pool is too small |
| `db_pool_checked_out{engine="primary"}` | Live primary connections; near `pool_size` = pressure |
| `db_pool_overflow{engine="primary"}` | Overflow in use — nearing exhaustion |
| `db_pool_checkouts_total` | Checkout rate — proxy for query throughput |

### 2. Per-route Redis metrics (admin dashboard)

```bash
curl -H "X-API-Key: $API_PASSWORD" http://localhost:8000/api/v1/admin/metrics/request-metrics/endpoint-stats
```

Returns p50/p95/p99 per route template, computed from a rolling 1000-sample window. Also available in the React admin UI at `/app` → Admin → Metrics.

### 3. Slow queries via pg_stat_statements

```bash
curl -H "X-API-Key: $API_PASSWORD" \
     "http://localhost:8000/api/v1/admin/db/slow-queries?limit=20&order_by=total_exec_time"
```

Returns the top-N slowest query fingerprints from `pg_stat_statements`. Reset with:

```bash
curl -X POST -H "X-API-Key: $API_PASSWORD" http://localhost:8000/api/v1/admin/db/slow-queries/reset
```

### 4. Request correlation

Every request gets an `X-Request-Id` header in the response and in every log line emitted during that request (`[<id>]` in the log format). To trace a specific slow request:

```bash
# From access log:
grep "a1b2c3d4..." /var/log/mediafusion.log

# From Redis recent-requests:
curl -H "X-API-Key: $API_PASSWORD" http://localhost:8000/api/v1/admin/metrics/request-metrics/recent
```

### 5. Per-request flamegraph (staging only)

Enable `ENABLE_PROFILER=true` in staging. Then hit any endpoint with `?_profile=1` and `X-API-Key: <password>` to get a pyinstrument HTML flamegraph:

```bash
curl -H "X-API-Key: $API_PASSWORD" \
     "http://localhost:8000/stream/movie/tt0111161.json?_profile=1" \
     -o flame.html && open flame.html
```

---

## Load testing

See `perf/README.md` for full instructions. Quick start:

```bash
pip install locust
locust -f perf/locustfile.py --headless -u 30 -r 5 -t 15m \
       --host http://localhost:8000 \
       --html perf/results/soak-$(date +%Y%m%d).html
```

The CI workflow (`.github/workflows/perf.yml`) runs a 60s smoke test on every PR that touches API/DB code.

---

## Known bottlenecks and their status

| Bottleneck | Status | Notes |
|---|---|---|
| Anonymous playback ORM read-modify-write | **Fixed** | Replaced with atomic `UPDATE ... SET playback_count = playback_count + 1` — no SELECT needed |
| stdlib `json` in stream cache layer | **Fixed** | Replaced with `orjson` in `db/crud/stream_services.py` and `manifest.py` |
| `Stream.name` missing trigram index (Torznab seq scan) | **Fixed** | Migration `a1b2c3d4e5f6` adds `idx_stream_name_trgm` GIN |
| PgBouncer primary prepared-statement protocol error | **Fixed** | `POSTGRES_USE_PGBOUNCER=true` disables asyncpg PS cache on primary engine |
| No request correlation IDs | **Fixed** | `RequestIdMiddleware` adds `X-Request-Id` and injects into logs |
| No HTTP latency histograms | **Fixed** | `utils/prometheus_metrics.py` + TimingMiddleware records to `/metrics` |
| No DB pool gauges | **Fixed** | SQLAlchemy PoolEvents publish to Prometheus |
| No `/ready` endpoint | **Fixed** | `/ready` checks DB + Redis, k8s readinessProbe updated |
| No profiler | **Fixed** | `?_profile=1` + pyinstrument behind `ENABLE_PROFILER=true` + admin key |
| No pg_stat_statements | **Fixed** | Enabled in deployment manifests + `/api/v1/admin/db/slow-queries` endpoint |
| Sequential Redis GET per cache key on stream bulk lookup | **Fixed** | Replaced N×`GET` loop with single `MGET` call in `_get_cached_movie/series_streams_bulk` |
| Sequential cache SET per DB miss | **Fixed** | Replaced N serial `SET` calls with a single pipelined `SET` batch via `_store_stream_cache_bulk` |
| Per-media_id session loop run sequentially | **Fixed** | `_fetch_movie/series_raw_streams_batch` now uses `asyncio.gather` — all media_ids fetched in parallel |
| Shared stream data cached per-user (cold cache for every user) | **Fixed** | Cache key uses `public` scope for non-user-specific data — all users share one cache entry per content |
| zlib compression level 6 on cache encode (CPU-heavy) | **Fixed** | Reduced to level 1 — same decompressibility, ~3× faster to encode |
| Live-search thundering herd (N users → N scraper fanouts) | **Fixed** | Redis `SETNX` lock (`live_search_lock:{id}`) ensures only one request scrapes per content item at a time |
| Live-search runs on every warm request (2s overhead per request) | **Fixed** | `SETNX` key now acts as a 5-min cooldown — first request in 5 min runs scrapers, all subsequent ones skip. Measured: warm stream latency 2000ms → 75ms |
| Torrent + usenet scrapers run sequentially in live search | **Fixed** | `_do_run_live_search_scrapers` runs both with `asyncio.gather` |
| Rate-limit pipeline uses `MULTI/EXEC` transaction | **Fixed** | Dropped `transaction=True` from INCR+EXPIRE pipeline — saves one round-trip per request |
| `/ready` endpoint postgres check fails (false 503) | **Fixed** | Changed from `get_read_session()` (async generator) to `get_read_session_context()` (async context manager) |
| Six separate queries per series stream-type | **Deferred** | Measure first with pg_stat_statements. Fix if confirmed as top-N slow query. |
| HPA ceiling vs. PG max_connections | **Mitigated** | PgBouncer multiplexing in `docker-compose-postgres-ha.yml`. K8s still needs tuning per scale target. |

---

## Diagnosing a DB pool exhaustion event (503 surge)

1. Check `http_requests_total{status_code="503"}` rising in Prometheus.
2. Check `db_pool_checked_out{engine="primary"}` — if it equals `pool_size + max_overflow`, pool is saturated.
3. Check `GET /api/v1/admin/db/slow-queries` — are there long-running queries holding connections?
4. Check `pg_stat_activity` directly:
   ```sql
   SELECT state, wait_event_type, wait_event, query
   FROM pg_stat_activity
   WHERE datname = current_database()
   ORDER BY query_start;
   ```
5. If connections are idle in transaction, look for long-held transactions across external HTTP calls.
6. Immediate relief: scale `GUNICORN_WORKERS` down (reduces connection demand) or scale `DB_MAX_CONNECTIONS` up (if PG can accept more).
7. Long-term: ensure PgBouncer is in front of primary and `POSTGRES_USE_PGBOUNCER=true` is set.

---

## Diagnosing high p95 on `/stream/...`

1. Enable `?_profile=1` on staging and hit the slow endpoint.
2. In the flamegraph, look for time spent in:
   - `_run_live_search_scrapers` → scraper fanout; consider disabling live search for the test or reducing scraper list
   - `_fetch_movie_raw_streams_batch` / `_fetch_series_raw_streams_batch` → DB batch fetches; measure with pg_stat_statements
   - `parse_stream_data` → debrid cache checks; these are external HTTP calls, measure per-provider
3. Check scraper health at `/api/v1/admin/metrics` → Scrapers.
4. Check Redis hit rates at `/api/v1/admin/metrics/redis`.

---

## Language migration decision point (post-optimizations)

After Phase E load tests, compare:

- **Baseline** (pre-optimizations): recorded in `perf/results/baseline-YYYY-MM-DD.md`
- **Post-optimizations**: record in `perf/results/post-opt-YYYY-MM-DD.md`

If p95 of `/stream/...` (cache hit, no live search) is < 500ms and DB pool exhaustion 503s are gone, the Python backend is sufficiently performant for current load.

If p95 is still > 1s on cache hits, identify the specific hot path from the flamegraph and consider a **strangler-fig** approach: rewrite only that path (e.g. stream resolution loop or a specific scraper) as a Go/Rust sidecar service, keeping everything else in Python.
