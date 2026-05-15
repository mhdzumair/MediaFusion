"""Prometheus metrics for HTTP requests and DB connection pool monitoring.

Extends the four business gauges already defined in api/routers/admin/metrics.py
with HTTP latency histograms, request counters, and per-engine pool gauges.
All metrics are registered in the default prometheus_client REGISTRY so
generate_latest() in the /metrics endpoint captures everything.
"""

import logging

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP request metrics
# ---------------------------------------------------------------------------

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "route", "status_code"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests completed",
    ["method", "route", "status_code"],
)

HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "HTTP requests currently being processed",
)

# ---------------------------------------------------------------------------
# DB connection pool metrics (labeled by engine: "primary" or "replica")
# ---------------------------------------------------------------------------

DB_POOL_SIZE = Gauge(
    "db_pool_size",
    "Configured SQLAlchemy pool_size (max steady-state connections per worker engine)",
    ["engine"],
)

DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_checked_out",
    "SQLAlchemy connections currently checked out from the pool",
    ["engine"],
)

DB_POOL_OVERFLOW = Gauge(
    "db_pool_overflow",
    "Overflow connections currently in use (above pool_size)",
    ["engine"],
)

DB_POOL_CHECKOUTS_TOTAL = Counter(
    "db_pool_checkouts_total",
    "Total DB connection check-outs",
    ["engine"],
)

DB_POOL_CHECKINS_TOTAL = Counter(
    "db_pool_checkins_total",
    "Total DB connection check-ins",
    ["engine"],
)

DB_POOL_ERRORS_TOTAL = Counter(
    "db_pool_errors_total",
    "Total DB pool errors (disconnections, checkout timeouts, etc.)",
    ["engine", "error_type"],
)


# ---------------------------------------------------------------------------
# Recording helpers
# ---------------------------------------------------------------------------


def record_http_metrics(method: str, route: str, status_code: int, duration: float) -> None:
    """Record one completed HTTP request into Prometheus metrics.

    Should be called from TimingMiddleware after a response is produced so
    every request (including 5xx) is captured.

    Route should be the FastAPI route template (e.g. '/stream/{catalog_type}/{video_id}'),
    not the raw path, to avoid unbounded cardinality.
    """
    label = (method, route, str(status_code))
    HTTP_REQUEST_DURATION.labels(*label).observe(duration)
    HTTP_REQUESTS_TOTAL.labels(*label).inc()


def register_pool_metrics(engine: AsyncEngine, engine_label: str) -> None:
    """Attach SQLAlchemy pool event listeners to export pool gauges for this engine.

    Call once per engine after creation.  The engine_label ('primary' or 'replica')
    is used as the Prometheus label value so both engines can be distinguished.
    """
    sync_engine = engine.sync_engine
    pool = sync_engine.pool

    # Set the static size label once at registration time.
    try:
        DB_POOL_SIZE.labels(engine=engine_label).set(pool.size())
    except Exception:
        pass

    @event.listens_for(sync_engine, "checkout")
    def _on_checkout(dbapi_conn, conn_record, conn_proxy):
        try:
            DB_POOL_CHECKED_OUT.labels(engine=engine_label).set(pool.checkedout())
            DB_POOL_OVERFLOW.labels(engine=engine_label).set(max(0, pool.overflow()))
            DB_POOL_CHECKOUTS_TOTAL.labels(engine=engine_label).inc()
        except Exception:
            pass

    @event.listens_for(sync_engine, "checkin")
    def _on_checkin(dbapi_conn, conn_record):
        try:
            DB_POOL_CHECKED_OUT.labels(engine=engine_label).set(pool.checkedout())
            DB_POOL_OVERFLOW.labels(engine=engine_label).set(max(0, pool.overflow()))
            DB_POOL_CHECKINS_TOTAL.labels(engine=engine_label).inc()
        except Exception:
            pass

    @event.listens_for(sync_engine, "connect")
    def _on_connect(dbapi_conn, conn_record):
        try:
            DB_POOL_SIZE.labels(engine=engine_label).set(pool.size())
        except Exception:
            pass

    logger.debug("Prometheus pool metrics registered for engine=%s", engine_label)
