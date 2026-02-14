"""Instance API routes package.

Provides endpoints for instance information (public vs private).
Import the router via get_router() to avoid circular imports.
"""

from fastapi import APIRouter

_router = None


def get_router() -> APIRouter:
    """Create and return the instance router.

    Uses lazy imports to avoid circular dependencies.
    """
    global _router
    if _router is not None:
        return _router

    from api.routers.instance.instance import router as instance_router

    combined = APIRouter()
    combined.include_router(instance_router)
    _router = combined
    return _router


__all__ = ["get_router"]
