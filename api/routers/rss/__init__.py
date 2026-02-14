"""RSS routes package.

Import the router via get_router() to avoid circular imports.
"""

from fastapi import APIRouter

_router = None


def get_router() -> APIRouter:
    """Create and return the combined RSS router.

    Uses lazy imports to avoid circular dependencies.
    """
    global _router
    if _router is not None:
        return _router

    from api.routers.rss.rss_feeds import router as rss_feeds_router
    from api.routers.rss.user_rss import router as user_rss_router

    combined = APIRouter()
    combined.include_router(rss_feeds_router)
    combined.include_router(user_rss_router)
    _router = combined
    return _router


# Don't auto-create router at import time to avoid circular imports
# Use get_router() instead when registering routes

__all__ = ["get_router"]
