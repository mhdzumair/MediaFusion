"""Stremio addon routes package.

Import the router via get_router() to avoid circular imports.
"""

from fastapi import APIRouter

_router = None


def get_router() -> APIRouter:
    """Create and return the combined stremio router.

    Uses lazy imports to avoid circular dependencies.
    """
    global _router
    if _router is not None:
        return _router

    from .catalog import router as catalog_router
    from .config import router as config_router
    from .core import router as core_router
    from .download import router as download_router
    from .manifest import router as manifest_router
    from .meta import router as meta_router
    from .poster import router as poster_router
    from .search import router as search_router
    from .stream import router as stream_router

    combined = APIRouter()
    combined.include_router(core_router)
    combined.include_router(manifest_router)
    combined.include_router(catalog_router)
    combined.include_router(search_router)
    combined.include_router(meta_router)
    combined.include_router(stream_router)
    combined.include_router(poster_router)
    combined.include_router(config_router)
    combined.include_router(download_router)
    _router = combined
    return _router


# Don't auto-create router at import time to avoid circular imports
# Use get_router() instead when registering routes

__all__ = ["get_router"]
