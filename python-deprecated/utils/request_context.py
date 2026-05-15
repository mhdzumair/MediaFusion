"""Per-request context variables shared across the middleware and logging layers."""

import contextvars

# Holds the current request's correlation ID, set by RequestIdMiddleware.
# Default "-" so the log format never raises KeyError on startup or background tasks.
REQUEST_ID_VAR: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
