"""User-related routes package.

Import the router via get_router() to avoid circular imports.
"""

from fastapi import APIRouter

_router = None


def get_router() -> APIRouter:
    """Create and return the combined user router.

    Uses lazy imports to avoid circular dependencies.
    """
    global _router
    if _router is not None:
        return _router

    # Import directly from module files to avoid circular imports
    from api.routers.user.auth import router as auth_router
    from api.routers.user.downloads import router as downloads_router
    from api.routers.user.indexers import router as indexers_router
    from api.routers.user.integrations import router as integrations_router
    from api.routers.user.profiles import router as profiles_router
    from api.routers.user.telegram import router as telegram_router
    from api.routers.user.telegram_auth import router as telegram_auth_router
    from api.routers.user.user import router as user_router
    from api.routers.user.user_catalogs import router as user_catalogs_router
    from api.routers.user.user_library import router as user_library_router
    from api.routers.user.watch_history import router as watch_history_router
    from api.routers.user.watchlist import router as watchlist_router

    combined = APIRouter()
    combined.include_router(auth_router)
    combined.include_router(user_router)
    combined.include_router(profiles_router)
    combined.include_router(indexers_router)
    combined.include_router(telegram_router)
    combined.include_router(telegram_auth_router)
    combined.include_router(watch_history_router)
    combined.include_router(downloads_router)
    combined.include_router(user_library_router)
    combined.include_router(user_catalogs_router)
    combined.include_router(integrations_router)
    combined.include_router(watchlist_router)
    _router = combined
    return _router


# Lazy property for backward compatibility
class _RouterModule:
    @property
    def router(self):
        return get_router()


# Don't auto-create router at import time to avoid circular imports
# Use get_router() instead when registering routes

__all__ = ["get_router"]
