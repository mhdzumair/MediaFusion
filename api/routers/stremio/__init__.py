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

    from .catalog import router as catalog_router  # noqa: PLC0415
    from .config import router as config_router  # noqa: PLC0415
    from .core import router as core_router  # noqa: PLC0415
    from .manifest import router as manifest_router  # noqa: PLC0415
    from .meta import router as meta_router  # noqa: PLC0415
    from .poster import router as poster_router  # noqa: PLC0415
    from .search import router as search_router  # noqa: PLC0415
    from .stream import router as stream_router  # noqa: PLC0415

    combined = APIRouter()
    combined.include_router(core_router)
    combined.include_router(manifest_router)
    combined.include_router(catalog_router)
    combined.include_router(search_router)
    combined.include_router(meta_router)
    combined.include_router(stream_router)
    combined.include_router(poster_router)
    combined.include_router(config_router)
    _router = combined
    return _router


# Don't auto-create router at import time to avoid circular imports
# Use get_router() instead when registering routes

__all__ = ["get_router"]
