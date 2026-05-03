"""Core routes: home, health, ready, favicon."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from db.config import settings
from utils import wrappers

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", tags=["home"])
async def get_home(request: Request):
    """Redirect to the React SPA home page."""
    return RedirectResponse(url="/app", status_code=302)


@router.get("/health", tags=["health"])
@wrappers.exclude_rate_limit
async def health():
    """Liveness check — returns 200 as long as the process is alive."""
    return {"status": "ok"}


@router.get("/ready", tags=["health"])
@wrappers.exclude_rate_limit
async def ready():
    """Readiness check — verifies DB and Redis are reachable.

    Returns 200 when the instance can serve traffic, 503 otherwise.
    Use this for Kubernetes readinessProbe instead of /health so pods are
    removed from the load-balancer during startup or dependency outages.
    """
    checks: dict[str, str] = {}
    healthy = True

    # --- Redis ---
    try:
        from db.redis_database import REDIS_ASYNC_CLIENT  # noqa: PLC0415

        await REDIS_ASYNC_CLIENT.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("Readiness check: Redis unavailable: %s", exc)
        checks["redis"] = "unavailable"
        healthy = False

    # --- Postgres ---
    try:
        from db.database import get_read_session_context  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        async with get_read_session_context() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        logger.warning("Readiness check: Postgres unavailable: %s", exc)
        checks["postgres"] = "unavailable"
        healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse(
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
        status_code=status_code,
    )


@router.get("/favicon.ico")
async def get_favicon():
    """Redirect to logo URL for favicon."""
    return RedirectResponse(url=settings.logo_url)
