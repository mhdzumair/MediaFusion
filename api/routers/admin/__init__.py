"""Admin routes package.

Import the router via get_router() to avoid circular imports.
"""

from fastapi import APIRouter

_router = None


def get_router() -> APIRouter:
    """Create and return the combined admin router.

    Uses lazy imports to avoid circular dependencies.
    """
    global _router
    if _router is not None:
        return _router

    from api.routers.admin.admin import router as admin_router
    from api.routers.admin.cache import router as cache_router
    from api.routers.admin.contribution_settings import (
        router as contribution_settings_router,
    )
    from api.routers.admin.database_admin import router as database_admin_router
    from api.routers.admin.exceptions import router as exceptions_router
    from api.routers.admin.metrics import router as metrics_router
    from api.routers.admin.scheduler_management import router as scheduler_router
    from api.routers.admin.scrapers import router as scrapers_router
    from api.routers.admin.telegram_admin import router as telegram_admin_router

    combined = APIRouter()
    combined.include_router(admin_router)
    combined.include_router(scheduler_router)
    combined.include_router(cache_router)
    combined.include_router(database_admin_router)
    combined.include_router(contribution_settings_router)
    combined.include_router(metrics_router)
    combined.include_router(scrapers_router)
    combined.include_router(telegram_admin_router)
    combined.include_router(exceptions_router)
    _router = combined
    return _router


# Don't auto-create router at import time to avoid circular imports
# Use get_router() instead when registering routes

__all__ = ["get_router"]
