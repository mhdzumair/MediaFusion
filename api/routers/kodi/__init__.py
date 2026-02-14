"""Kodi device pairing routes package.

Import the router via get_router() to avoid circular imports.
"""

from fastapi import APIRouter

_router = None


def get_router() -> APIRouter:
    """Create and return the combined Kodi router.

    Uses lazy imports to avoid circular dependencies.
    """
    global _router
    if _router is not None:
        return _router

    from .setup import router as setup_router

    combined = APIRouter()
    combined.include_router(setup_router)
    _router = combined
    return _router


# Don't auto-create router at import time to avoid circular imports
# Use get_router() instead when registering routes

__all__ = ["get_router"]
